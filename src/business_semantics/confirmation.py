"""Compile user selections into a DRAFT config and validate only read samples."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .discovery import DiscoverySnapshot, FieldMetadata, SourceRecord
from .errors import SourceConfigValidationError, SourceDryRunError
from .models import (
    ConnectorConfig,
    DataSourceConfig,
    FieldMapping,
    ProductReference,
    ProductReferenceStrategy,
    SourceStatus,
    TableMapping,
    normalize_source_type,
)
from .repository import SourceConfigRepository


@dataclass(frozen=True)
class MappingSelection:
    product_table_id: str
    task_table_id: str
    product_fields: Mapping[str, str]
    task_fields: Mapping[str, str]
    writeback_fields: Mapping[str, str] = field(default_factory=dict)
    product_reference_strategy: ProductReferenceStrategy = ProductReferenceStrategy.AUTO


@dataclass(frozen=True)
class DryRunReport:
    product_samples: int
    task_samples: int
    issues: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class ConfirmationResult:
    config: DataSourceConfig
    dry_run: DryRunReport


def _mapping_from_field(field: FieldMetadata) -> FieldMapping:
    return FieldMapping(
        field_id=field.field_id,
        last_known_name=field.name,
        source_type=field.normalized_type,
        required=True,
    )


def _selected_mappings(
    snapshot: DiscoverySnapshot,
    table_id: str,
    selected: Mapping[str, str],
) -> dict[str, FieldMapping]:
    table = snapshot.table_by_id(table_id)
    metadata = {field.field_id: field for field in table.fields}
    mappings: dict[str, FieldMapping] = {}
    for semantic_name, field_id in selected.items():
        field = metadata.get(field_id)
        if field is None:
            raise SourceConfigValidationError(
                "确认映射使用了未发现的字段", table_id=table_id,
                semantic_name=semantic_name, field_id=field_id,
            )
        mappings[str(semantic_name)] = _mapping_from_field(field)
    return mappings


def compile_draft_config(
    snapshot: DiscoverySnapshot,
    selection: MappingSelection,
    *,
    revision: int,
) -> DataSourceConfig:
    """Compile a user-confirmed selection; this function never mutates a Base."""
    products = snapshot.table_by_id(selection.product_table_id)
    tasks = snapshot.table_by_id(selection.task_table_id)
    product_fields = _selected_mappings(snapshot, products.table.table_id, selection.product_fields)
    task_fields = _selected_mappings(snapshot, tasks.table.table_id, selection.task_fields)
    writeback = _selected_mappings(snapshot, tasks.table.table_id, selection.writeback_fields)
    config = DataSourceConfig(
        source_id=snapshot.source_id,
        display_name=snapshot.display_name,
        connector=ConnectorConfig("feishu_base", snapshot.base_token, snapshot.credential_ref),
        revision=revision,
        status=SourceStatus.DRAFT,
        schema_fingerprint=snapshot.schema_fingerprint,
        tables={
            "products": TableMapping(products.table.table_id, "product_id", product_fields),
            "tasks": TableMapping(tasks.table.table_id, "task_id", task_fields),
        },
        writeback=writeback,
        product_reference=ProductReference(selection.product_reference_strategy),
    )
    config.validate()
    return config


def _cell(record: SourceRecord, field: FieldMetadata) -> Any:
    return record.fields.get(field.field_id, record.fields.get(field.name))


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d*\.\d+|\d+", str(value).replace("￥", "").replace("元", ""))
    return float(match.group(0)) if match else None


def _validate_required_samples(
    *,
    role: str,
    table_fields: Mapping[str, FieldMetadata],
    records: tuple[SourceRecord, ...],
    required_semantics: set[str],
) -> list[Mapping[str, Any]]:
    issues: list[Mapping[str, Any]] = []
    if not records:
        return [{"code": "SAMPLE_MISSING", "table": role, "message": "表中没有可供确认的样本记录"}]
    for index, record in enumerate(records):
        for semantic in required_semantics:
            field = table_fields[semantic]
            value = _cell(record, field)
            if not _has_value(value):
                issues.append({
                    "code": "REQUIRED_VALUE_MISSING", "table": role, "semantic": semantic,
                    "record_id": record.record_id or f"sample-{index + 1}",
                })
            elif semantic in {"min_price", "promo_price"} and _number(value) is None:
                issues.append({
                    "code": "VALUE_CONVERSION_FAILED", "table": role, "semantic": semantic,
                    "record_id": record.record_id or f"sample-{index + 1}", "value": str(value),
                })
    return issues


def validate_draft_samples(config: DataSourceConfig, snapshot: DiscoverySnapshot) -> DryRunReport:
    """Validate selected fields against cached discovery samples without a write call."""
    config.validate()
    product_table = snapshot.table_by_id(config.tables["products"].table_id)
    task_table = snapshot.table_by_id(config.tables["tasks"].table_id)
    product_metadata = {field.field_id: field for field in product_table.fields}
    task_metadata = {field.field_id: field for field in task_table.fields}
    product_fields = {
        semantic: product_metadata[mapping.field_id]
        for semantic, mapping in config.tables["products"].fields.items()
    }
    task_fields = {
        semantic: task_metadata[mapping.field_id]
        for semantic, mapping in config.tables["tasks"].fields.items()
    }
    issues = _validate_required_samples(
        role="products", table_fields=product_fields, records=product_table.samples,
        required_semantics={"product_id", "product_name", "min_price"},
    )
    issues.extend(_validate_required_samples(
        role="tasks", table_fields=task_fields, records=task_table.samples,
        required_semantics={"task_id", "product_id", "img_type", "aspect_ratio", "main_text", "promo_price", "deploy_date"},
    ))

    product_ids = {
        str(_cell(record, product_fields["product_id"])).strip()
        for record in product_table.samples if _has_value(_cell(record, product_fields["product_id"]))
    }
    if len(product_ids) != len(product_table.samples):
        issues.append({"code": "PRODUCT_ID_NOT_UNIQUE", "table": "products", "message": "样本商品 ID 存在重复"})

    reference_field = task_fields[config.product_reference.task_field]
    reference_type = normalize_source_type(reference_field.source_type)
    strategy = config.product_reference.strategy
    if strategy == ProductReferenceStrategy.AUTO:
        strategy = (
            ProductReferenceStrategy.LINKED_RECORD
            if reference_type == "linked_record" else ProductReferenceStrategy.DIRECT_VALUE
        )
    if strategy == ProductReferenceStrategy.LINKED_RECORD:
        for index, record in enumerate(task_table.samples):
            if not _has_value(_cell(record, reference_field)):
                issues.append({"code": "PRODUCT_REFERENCE_MISSING", "table": "tasks", "record_id": record.record_id or f"sample-{index + 1}"})
    else:
        for index, record in enumerate(task_table.samples):
            value = _cell(record, reference_field)
            if _has_value(value) and product_ids and str(value).strip() not in product_ids:
                issues.append({
                    "code": "PRODUCT_REFERENCE_NOT_IN_SAMPLE", "table": "tasks",
                    "record_id": record.record_id or f"sample-{index + 1}", "product_id": str(value).strip(),
                })
    return DryRunReport(len(product_table.samples), len(task_table.samples), tuple(issues))


def confirm_draft_config(
    repository: SourceConfigRepository,
    snapshot: DiscoverySnapshot,
    selection: MappingSelection,
    *,
    revision: int,
    expected_active_revision: int | None = None,
) -> ConfirmationResult:
    """Persist a verified DRAFT revision. Activation is intentionally separate."""
    config = compile_draft_config(snapshot, selection, revision=revision)
    report = validate_draft_samples(config, snapshot)
    if not report.passed:
        raise SourceDryRunError(config.source_id, list(report.issues))
    repository.save(config, expected_active_revision=expected_active_revision)
    repository.mark_dry_run_verified(
        config.source_id,
        config.revision,
        evidence={
            "product_samples": report.product_samples,
            "task_samples": report.task_samples,
            "schema_fingerprint": config.schema_fingerprint,
        },
    )
    return ConfirmationResult(config, report)
