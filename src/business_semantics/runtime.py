"""Deep runtime module: resolve one configured source task into canonical input."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .discovery import (
    DiscoveredTable,
    DiscoverySnapshot,
    FieldMetadata,
    TableMetadata,
    ensure_schema_compatible,
    normalize_field_metadata,
    normalize_sample_records,
    normalize_table_metadata,
    schema_fingerprint,
)
from .errors import SourceRecordLookupError, SourceValueConversionError
from .models import (
    DataSourceConfig,
    PreparedTask,
    ProductReferenceStrategy,
    SourceReceipt,
    StandardMarketingTask,
    StandardProduct,
    normalize_source_type,
)
from .repository import SourceConfigRepository


class FeishuDataGateway(Protocol):
    """Read-only subset already supplied by the existing OpenAPI and CLI adapters."""

    def list_tables(self, base_token: str) -> Any: ...

    def list_fields(self, base_token: str, table_id: str) -> Any: ...

    def list_records(self, base_token: str, table_id: str) -> Any: ...


def _schema_snapshot(gateway: FeishuDataGateway, config: DataSourceConfig) -> DiscoverySnapshot:
    tables: list[DiscoveredTable] = []
    for table in normalize_table_metadata(gateway.list_tables(config.connector.base_token)):
        fields = tuple(normalize_field_metadata(
            gateway.list_fields(config.connector.base_token, table.table_id)
        ))
        tables.append(DiscoveredTable(table, fields, (), {}))
    snapshot = DiscoverySnapshot(
        source_id=config.source_id,
        display_name=config.display_name,
        base_token=config.connector.base_token,
        credential_ref=config.connector.credential_ref,
        tables=tuple(tables),
        schema_fingerprint="",
    )
    return DiscoverySnapshot(**{**snapshot.__dict__, "schema_fingerprint": schema_fingerprint(snapshot.tables)})


def _field_index(snapshot: DiscoverySnapshot, table_id: str) -> Mapping[str, FieldMetadata]:
    return {field.field_id: field for field in snapshot.table_by_id(table_id).fields}


def _cell(record: Mapping[str, Any], field: FieldMetadata) -> Any:
    return record.get(field.field_id, record.get(field.name))


def _scalar(value: Any, *, source_id: str, semantic: str) -> Any:
    if value is None or value == "" or value == []:
        raise SourceValueConversionError(source_id, semantic, "必填字段为空")
    if isinstance(value, list):
        if len(value) != 1:
            raise SourceValueConversionError(source_id, semantic, "字段必须是单值", value=str(value))
        return _scalar(value[0], source_id=source_id, semantic=semantic)
    if isinstance(value, Mapping):
        for key in ("text", "name", "value"):
            if key in value:
                return _scalar(value[key], source_id=source_id, semantic=semantic)
        raise SourceValueConversionError(source_id, semantic, "无法从对象单元格提取标量", value=str(value))
    if isinstance(value, bool):
        raise SourceValueConversionError(source_id, semantic, "布尔值不能转换为业务字段", value=str(value))
    return value


def _text(value: Any, *, source_id: str, semantic: str) -> str:
    scalar = _scalar(value, source_id=source_id, semantic=semantic)
    if isinstance(scalar, float) and scalar.is_integer():
        text = str(int(scalar))
    else:
        text = str(scalar).strip()
    if not text:
        raise SourceValueConversionError(source_id, semantic, "文本字段为空")
    return text


def _number(value: Any, *, source_id: str, semantic: str) -> float:
    scalar = _scalar(value, source_id=source_id, semantic=semantic)
    if isinstance(scalar, (int, float)) and not isinstance(scalar, bool):
        return float(scalar)
    text = str(scalar).strip().replace("￥", "").replace("元", "").replace(",", "")
    if not re.fullmatch(r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)", text):
        raise SourceValueConversionError(source_id, semantic, "无法严格转换为数字", value=str(scalar))
    return float(text)


def _date(value: Any, *, source_id: str, semantic: str) -> str:
    scalar = _scalar(value, source_id=source_id, semantic=semantic)
    if isinstance(scalar, (int, float)) and not isinstance(scalar, bool):
        milliseconds = float(scalar)
        if milliseconds < 10_000_000_000:
            milliseconds *= 1000
        return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).date().isoformat()
    text = str(scalar).strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise SourceValueConversionError(source_id, semantic, "无法严格转换为日期", value=text) from exc


def _linked_record_id(value: Any, *, source_id: str, semantic: str) -> str:
    if isinstance(value, Mapping):
        for key in ("record_id", "recordId", "id"):
            if value.get(key):
                return str(value[key])
        if "value" in value:
            return _linked_record_id(value["value"], source_id=source_id, semantic=semantic)
    if isinstance(value, list):
        if len(value) != 1:
            raise SourceValueConversionError(source_id, semantic, "关联商品必须且只能指向一条记录", value=str(value))
        return _linked_record_id(value[0], source_id=source_id, semantic=semantic)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise SourceValueConversionError(source_id, semantic, "无法解析关联商品记录", value=str(value))


def _attachment(value: Any, *, source_id: str, semantic: str) -> Any:
    if value is None or value == "":
        return None
    if not isinstance(value, (list, Mapping, str)):
        raise SourceValueConversionError(source_id, semantic, "附件字段格式不受支持", value=str(value))
    return value


def _optional_text(record: Mapping[str, Any], field: FieldMetadata | None, *, source_id: str, semantic: str) -> str | None:
    if field is None:
        return None
    value = _cell(record, field)
    return None if value is None or value == "" or value == [] else _text(value, source_id=source_id, semantic=semantic)


def _records(gateway: FeishuDataGateway, config: DataSourceConfig, table_id: str) -> list[Mapping[str, Any]]:
    records = normalize_sample_records(gateway.list_records(config.connector.base_token, table_id))
    return [dict(record.fields, _record_id=record.record_id) for record in records]


def _exact_matches(
    records: list[Mapping[str, Any]],
    field: FieldMetadata,
    value: str,
    *,
    source_id: str,
) -> list[Mapping[str, Any]]:
    matches: list[Mapping[str, Any]] = []
    for record in records:
        try:
            candidate = _text(_cell(record, field), source_id=source_id, semantic="lookup_key")
        except SourceValueConversionError:
            continue
        if candidate == value:
            matches.append(record)
    return matches


def _require_exact(
    matches: list[Mapping[str, Any]], *, source_id: str, identifier: str, value: str,
    missing_code: str, duplicate_code: str,
) -> Mapping[str, Any]:
    if not matches:
        raise SourceRecordLookupError(missing_code, source_id, identifier, value, 0)
    if len(matches) > 1:
        raise SourceRecordLookupError(duplicate_code, source_id, identifier, value, len(matches))
    return matches[0]


def _fingerprint(config: DataSourceConfig, task: StandardMarketingTask, product: StandardProduct) -> str:
    payload = {
        "source_id": config.source_id,
        "config_revision": config.revision,
        "schema_fingerprint": config.schema_fingerprint,
        "task": asdict(task),
        "product": asdict(product),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ConfiguredSourceRuntime:
    """The single deep-module interface for precise, read-only task preparation."""

    def __init__(self, repository: SourceConfigRepository, gateway: FeishuDataGateway) -> None:
        self._repository = repository
        self._gateway = gateway

    def prepare_task(self, source_id: str, task_id: str) -> PreparedTask:
        if not str(task_id).strip():
            raise SourceValueConversionError(source_id, "task_id", "任务 ID 不能为空")
        config = self._repository.get_active(source_id)
        config.require_active()
        snapshot = _schema_snapshot(self._gateway, config)
        ensure_schema_compatible(config, snapshot)

        task_mapping = config.tables["tasks"]
        product_mapping = config.tables["products"]
        task_fields = _field_index(snapshot, task_mapping.table_id)
        product_fields = _field_index(snapshot, product_mapping.table_id)
        task_records = _records(self._gateway, config, task_mapping.table_id)
        task_record = _require_exact(
            _exact_matches(task_records, task_fields[task_mapping.fields["task_id"].field_id], str(task_id).strip(), source_id=source_id),
            source_id=source_id, identifier="task_id", value=str(task_id).strip(),
            missing_code="TASK_NOT_FOUND", duplicate_code="DUPLICATE_TASK_ID",
        )

        task_values = {
            semantic: _cell(task_record, task_fields[mapping.field_id])
            for semantic, mapping in task_mapping.fields.items()
        }
        actual_task_id = _text(task_values["task_id"], source_id=source_id, semantic="task_id")
        product_reference = config.product_reference.strategy
        reference_field = task_fields[task_mapping.fields[config.product_reference.task_field].field_id]
        if product_reference == ProductReferenceStrategy.AUTO:
            product_reference = (
                ProductReferenceStrategy.LINKED_RECORD
                if normalize_source_type(reference_field.source_type) == "linked_record"
                else ProductReferenceStrategy.DIRECT_VALUE
            )
        product_records = _records(self._gateway, config, product_mapping.table_id)
        if product_reference == ProductReferenceStrategy.LINKED_RECORD:
            record_id = _linked_record_id(task_values[config.product_reference.task_field], source_id=source_id, semantic="product_id")
            linked = [record for record in product_records if str(record.get("_record_id") or "") == record_id]
            product_record = _require_exact(
                linked, source_id=source_id, identifier="product_record_id", value=record_id,
                missing_code="PRODUCT_NOT_FOUND", duplicate_code="DUPLICATE_PRODUCT_RECORD_ID",
            )
        else:
            product_id = _text(task_values[config.product_reference.task_field], source_id=source_id, semantic="product_id")
            product_record = _require_exact(
                _exact_matches(product_records, product_fields[product_mapping.fields["product_id"].field_id], product_id, source_id=source_id),
                source_id=source_id, identifier="product_id", value=product_id,
                missing_code="PRODUCT_NOT_FOUND", duplicate_code="DUPLICATE_PRODUCT_ID",
            )

        product_values = {
            semantic: _cell(product_record, product_fields[mapping.field_id])
            for semantic, mapping in product_mapping.fields.items()
        }
        product = StandardProduct(
            product_id=_text(product_values["product_id"], source_id=source_id, semantic="product_id"),
            product_name=_text(product_values["product_name"], source_id=source_id, semantic="product_name"),
            min_price=_number(product_values["min_price"], source_id=source_id, semantic="min_price"),
            sku=_optional_text(product_record, product_fields.get(product_mapping.fields.get("sku", None).field_id) if product_mapping.fields.get("sku") else None, source_id=source_id, semantic="sku"),
            regular_price=_number(product_values["regular_price"], source_id=source_id, semantic="regular_price") if "regular_price" in product_values else None,
            selling_points=_optional_text(product_record, product_fields.get(product_mapping.fields.get("selling_points", None).field_id) if product_mapping.fields.get("selling_points") else None, source_id=source_id, semantic="selling_points"),
            product_image=_attachment(product_values["product_image"], source_id=source_id, semantic="product_image") if "product_image" in product_values else None,
            logo_image=_attachment(product_values["logo_image"], source_id=source_id, semantic="logo_image") if "logo_image" in product_values else None,
            attributes={name: value for name, value in product_values.items() if name in {"color", "capacity"}},
        )
        task = StandardMarketingTask(
            task_id=actual_task_id,
            product_id=product.product_id,
            img_type=_text(task_values["img_type"], source_id=source_id, semantic="img_type"),
            aspect_ratio=_text(task_values["aspect_ratio"], source_id=source_id, semantic="aspect_ratio"),
            deploy_date=_date(task_values["deploy_date"], source_id=source_id, semantic="deploy_date"),
            main_text=_text(task_values["main_text"], source_id=source_id, semantic="main_text"),
            sub_text=_optional_text(task_record, task_fields.get(task_mapping.fields.get("sub_text", None).field_id) if task_mapping.fields.get("sub_text") else None, source_id=source_id, semantic="sub_text") or "",
            campaign_name=_optional_text(task_record, task_fields.get(task_mapping.fields.get("campaign_name", None).field_id) if task_mapping.fields.get("campaign_name") else None, source_id=source_id, semantic="campaign_name"),
            campaign_start=_date(task_values["campaign_start"], source_id=source_id, semantic="campaign_start") if "campaign_start" in task_values else None,
            campaign_end=_date(task_values["campaign_end"], source_id=source_id, semantic="campaign_end") if "campaign_end" in task_values else None,
            promo_price=_number(task_values["promo_price"], source_id=source_id, semantic="promo_price"),
        )
        product_record_id = str(product_record.get("_record_id") or "")
        task_record_id = str(task_record.get("_record_id") or "")
        if not product_record_id or not task_record_id:
            raise SourceValueConversionError(source_id, "record_id", "飞书记录缺少 record_id")
        receipt = SourceReceipt(
            source_id, config.revision, product_mapping.table_id, product_record_id,
            task_mapping.table_id, task_record_id,
        )
        return PreparedTask(
            source_id, config.revision, task, product, receipt, config.schema_fingerprint,
            input_fingerprint=_fingerprint(config, task, product),
        )
