"""Read-only Feishu schema discovery and deterministic mapping recommendations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence

from .errors import SourceConfigValidationError, SourceSchemaDriftError
from .models import DataSourceConfig, normalize_source_type


class FeishuDiscoveryClient(Protocol):
    """Small read-only seam implemented by the existing Feishu clients."""

    def list_tables(self, base_token: str) -> Any: ...

    def list_fields(self, base_token: str, table_id: str) -> Any: ...

    def list_records_sample(self, base_token: str, table_id: str, limit: int) -> Any: ...


@dataclass(frozen=True)
class TableMetadata:
    table_id: str
    name: str


@dataclass(frozen=True)
class FieldMetadata:
    field_id: str
    name: str
    source_type: str

    @property
    def normalized_type(self) -> str:
        return normalize_source_type(self.source_type)


@dataclass(frozen=True)
class SourceRecord:
    record_id: str
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class FieldProfile:
    sample_count: int
    non_empty_rate: float
    unique_rate: float


@dataclass(frozen=True)
class DiscoveredTable:
    table: TableMetadata
    fields: tuple[FieldMetadata, ...]
    samples: tuple[SourceRecord, ...]
    profiles: Mapping[str, FieldProfile]


@dataclass(frozen=True)
class DiscoverySnapshot:
    source_id: str
    display_name: str
    base_token: str
    credential_ref: str
    tables: tuple[DiscoveredTable, ...]
    schema_fingerprint: str

    def table_by_id(self, table_id: str) -> DiscoveredTable:
        for table in self.tables:
            if table.table.table_id == table_id:
                return table
        raise SourceConfigValidationError("未在发现快照中找到数据表", table_id=table_id)


class CandidateConfidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True)
class MappingCandidate:
    semantic_name: str
    table_id: str
    field_id: str
    field_name: str
    source_type: str
    score: float
    confidence: CandidateConfidence
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "semantic_name": self.semantic_name,
            "table_id": self.table_id,
            "field_id": self.field_id,
            "field_name": self.field_name,
            "source_type": self.source_type,
            "score": self.score,
            "confidence": self.confidence.value,
            "reasons": list(self.reasons),
        }


SEMANTIC_ALIASES: Mapping[str, tuple[str, ...]] = {
    "product_id": ("商品id", "商品编号", "商品编码", "产品id", "product_id", "sku"),
    "product_name": ("商品名称", "产品名称", "商品", "品名", "product_name"),
    "min_price": ("最低允许促销价", "最低价", "营销底价", "底价", "min_price"),
    "product_image": ("商品主图", "产品图", "商品图片", "product_image"),
    "logo_image": ("品牌 Logo", "品牌标识", "logo", "logo_image"),
    "task_id": ("任务id", "工单编号", "任务编号", "编号", "task_id"),
    "img_type": ("图片类型", "图片类型", "类型", "img_type"),
    "aspect_ratio": ("画布比例", "比例", "尺寸", "aspect_ratio"),
    "main_text": ("主文案", "文案", "宣传语", "广告语", "标题", "main_text"),
    "sub_text": ("补充要求", "副文案", "备注", "sub_text"),
    "promo_price": ("活动价", "折后价", "促销价", "现价", "promo_price"),
    "deploy_date": ("投放日期", "投放时间", "deploy_date"),
    "campaign_name": ("活动名称", "活动", "campaign_name"),
    "campaign_start": ("活动开始日期", "活动开始时间", "开始日期", "campaign_start"),
    "campaign_end": ("活动结束日期", "活动结束时间", "结束日期", "campaign_end"),
    "template_id": ("模板", "模板id", "设计模板", "template_id"),
    # 回写字段同样通过发现流程选择，避免把某个 Base 的中文列名写死在运行时。
    "status": ("审查状态", "处理状态", "状态", "status"),
    "issues": ("问题说明", "审查问题", "违规说明", "issues"),
    "processed_at": ("处理时间", "审查时间", "完成时间", "processed_at"),
    "input_hash": ("输入指纹", "任务指纹", "input_hash"),
    "pipeline_version": ("流水线版本", "处理版本", "pipeline_version"),
    "image_attachment": ("生成图片", "合规图片", "图片附件", "image_attachment"),
    "image_url": ("图片链接", "生成图片链接", "image_url"),
}

EXPECTED_TYPES: Mapping[str, set[str]] = {
    "product_id": {"text", "number", "linked_record"},
    "product_name": {"text"}, "min_price": {"text", "number"},
    "product_image": {"attachment"}, "logo_image": {"attachment"},
    "task_id": {"text", "number"}, "img_type": {"text"},
    "aspect_ratio": {"text"}, "main_text": {"text"}, "sub_text": {"text"},
    "promo_price": {"text", "number"}, "deploy_date": {"text", "date"},
    "campaign_name": {"text"}, "campaign_start": {"text", "date"},
    "campaign_end": {"text", "date"},
    "template_id": {"text", "single_select"},
    "status": {"text", "single_select"}, "issues": {"text"}, "processed_at": {"text", "date"},
    "input_hash": {"text"}, "pipeline_version": {"text"},
    "image_attachment": {"attachment"}, "image_url": {"text"},
}


def _collection(raw: Any, *keys: str) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, Mapping):
        return []
    for container in (raw, raw.get("data") if isinstance(raw.get("data"), Mapping) else {}):
        for key in keys:
            value = container.get(key)
            if isinstance(value, list):
                return value
    return []


def normalize_table_metadata(raw: Any) -> list[TableMetadata]:
    result: list[TableMetadata] = []
    for item in _collection(raw, "items", "tables"):
        if not isinstance(item, Mapping):
            continue
        table_id = str(item.get("table_id") or item.get("tableId") or item.get("id") or "")
        name = str(item.get("name") or item.get("table_name") or item.get("tableName") or "")
        if table_id and name:
            result.append(TableMetadata(table_id, name))
    return result


def normalize_field_metadata(raw: Any) -> list[FieldMetadata]:
    result: list[FieldMetadata] = []
    for item in _collection(raw, "items", "fields"):
        if not isinstance(item, Mapping):
            continue
        field_id = str(item.get("field_id") or item.get("fieldId") or item.get("id") or "")
        name = str(item.get("field_name") or item.get("fieldName") or item.get("name") or "")
        source_type = str(item.get("type") or item.get("field_type") or item.get("fieldType") or "")
        if field_id and name and source_type:
            result.append(FieldMetadata(field_id, name, source_type))
    return result


def normalize_sample_records(raw: Any) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for item in _collection(raw, "items", "records"):
        if not isinstance(item, Mapping):
            continue
        fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else item
        record_id = str(item.get("record_id") or item.get("recordId") or item.get("_record_id") or "")
        records.append(SourceRecord(record_id, dict(fields)))
    return records


def _cell(record: SourceRecord, field: FieldMetadata) -> Any:
    return record.fields.get(field.field_id, record.fields.get(field.name))


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _stable_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _profile(field: FieldMetadata, samples: Sequence[SourceRecord]) -> FieldProfile:
    values = [_cell(record, field) for record in samples]
    populated = [value for value in values if _has_value(value)]
    unique = {_stable_value(value) for value in populated}
    return FieldProfile(
        sample_count=len(samples),
        non_empty_rate=round(len(populated) / len(samples), 3) if samples else 0.0,
        unique_rate=round(len(unique) / len(populated), 3) if populated else 0.0,
    )


def schema_fingerprint(tables: Sequence[DiscoveredTable]) -> str:
    """Names intentionally do not participate: field renames are compatible."""
    schema = [
        {
            "table_id": table.table.table_id,
            "fields": sorted(
                ({"field_id": field.field_id, "type": field.normalized_type} for field in table.fields),
                key=lambda item: item["field_id"],
            ),
        }
        for table in sorted(tables, key=lambda item: item.table.table_id)
    ]
    encoded = json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def discover_source(
    client: FeishuDiscoveryClient,
    *,
    source_id: str,
    display_name: str,
    base_token: str,
    credential_ref: str,
    sample_limit: int = 5,
) -> DiscoverySnapshot:
    if sample_limit < 1 or sample_limit > 20:
        raise ValueError("sample_limit 必须在 1 到 20 之间")
    tables: list[DiscoveredTable] = []
    for table in normalize_table_metadata(client.list_tables(base_token)):
        fields = tuple(normalize_field_metadata(client.list_fields(base_token, table.table_id)))
        samples = tuple(normalize_sample_records(
            client.list_records_sample(base_token, table.table_id, sample_limit)
        ))
        profiles = {field.field_id: _profile(field, samples) for field in fields}
        tables.append(DiscoveredTable(table, fields, samples, profiles))
    if not tables:
        raise SourceConfigValidationError("发现结果中没有可用数据表", source_id=source_id)
    snapshot = DiscoverySnapshot(
        source_id=source_id,
        display_name=display_name,
        base_token=base_token,
        credential_ref=credential_ref,
        tables=tuple(tables),
        schema_fingerprint="",
    )
    return DiscoverySnapshot(**{**snapshot.__dict__, "schema_fingerprint": schema_fingerprint(snapshot.tables)})


def _normal_name(value: str) -> str:
    return "".join(str(value).lower().strip().replace("_", "").replace("-", "").split())


def mapping_candidates(snapshot: DiscoverySnapshot) -> list[MappingCandidate]:
    candidates: list[MappingCandidate] = []
    for table in snapshot.tables:
        for field in table.fields:
            profile = table.profiles[field.field_id]
            actual_type = field.normalized_type
            field_name = _normal_name(field.name)
            for semantic_name, aliases in SEMANTIC_ALIASES.items():
                normalized_aliases = [_normal_name(alias) for alias in aliases]
                score = 0.0
                reasons: list[str] = []
                if field_name in normalized_aliases:
                    score += 0.55
                    reasons.append("字段名与标准别名完全匹配")
                elif any(alias and (alias in field_name or field_name in alias) for alias in normalized_aliases):
                    score += 0.32
                    reasons.append("字段名与标准别名部分匹配")
                else:
                    continue
                if actual_type in EXPECTED_TYPES.get(semantic_name, set()):
                    score += 0.2
                    reasons.append(f"字段类型 {actual_type} 与该语义兼容")
                else:
                    reasons.append(f"字段类型 {actual_type} 与该语义不匹配")
                if profile.non_empty_rate >= 0.8:
                    score += 0.1
                    reasons.append(f"样本非空率 {profile.non_empty_rate:.0%}")
                if semantic_name in {"product_id", "task_id"} and profile.unique_rate >= 0.95:
                    score += 0.15
                    reasons.append(f"样本唯一率 {profile.unique_rate:.0%}")
                score = round(min(score, 1.0), 2)
                confidence = (
                    CandidateConfidence.HIGH if score >= 0.8 else
                    CandidateConfidence.MEDIUM if score >= 0.55 else
                    CandidateConfidence.LOW
                )
                candidates.append(MappingCandidate(
                    semantic_name, table.table.table_id, field.field_id, field.name,
                    actual_type, score, confidence, tuple(reasons),
                ))
    return sorted(candidates, key=lambda item: (item.semantic_name, -item.score, item.table_id, item.field_id))


@dataclass(frozen=True)
class SchemaDriftReport:
    expected_fingerprint: str
    current_fingerprint: str
    issues: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @property
    def is_compatible(self) -> bool:
        return not self.issues


def validate_schema_drift(config: DataSourceConfig, snapshot: DiscoverySnapshot) -> SchemaDriftReport:
    issues: list[Mapping[str, Any]] = []
    current_tables = {table.table.table_id: table for table in snapshot.tables}
    for role, table_mapping in config.tables.items():
        discovered = current_tables.get(table_mapping.table_id)
        if discovered is None:
            issues.append({"code": "TABLE_MISSING", "table": role, "table_id": table_mapping.table_id})
            continue
        current_fields = {field.field_id: field for field in discovered.fields}
        for semantic_name, mapping in table_mapping.fields.items():
            field = current_fields.get(mapping.field_id)
            if field is None:
                issues.append({
                    "code": "FIELD_MISSING", "table": role, "semantic": semantic_name,
                    "field_id": mapping.field_id,
                })
            elif normalize_source_type(mapping.source_type) != field.normalized_type:
                issues.append({
                    "code": "FIELD_TYPE_INCOMPATIBLE", "table": role, "semantic": semantic_name,
                    "field_id": mapping.field_id, "expected_type": normalize_source_type(mapping.source_type),
                    "actual_type": field.normalized_type,
                })
    task_table = current_tables.get(config.tables["tasks"].table_id)
    if task_table is not None:
        current_fields = {field.field_id: field for field in task_table.fields}
        for semantic_name, mapping in config.writeback.items():
            field = current_fields.get(mapping.field_id)
            if field is None:
                issues.append({
                    "code": "FIELD_MISSING", "table": "writeback", "semantic": semantic_name,
                    "field_id": mapping.field_id,
                })
            elif normalize_source_type(mapping.source_type) != field.normalized_type:
                issues.append({
                    "code": "FIELD_TYPE_INCOMPATIBLE", "table": "writeback", "semantic": semantic_name,
                    "field_id": mapping.field_id,
                    "expected_type": normalize_source_type(mapping.source_type),
                    "actual_type": field.normalized_type,
                })
    return SchemaDriftReport(config.schema_fingerprint, snapshot.schema_fingerprint, tuple(issues))


def ensure_schema_compatible(config: DataSourceConfig, snapshot: DiscoverySnapshot) -> SchemaDriftReport:
    report = validate_schema_drift(config, snapshot)
    if not report.is_compatible:
        raise SourceSchemaDriftError(config.source_id, list(report.issues))
    return report
