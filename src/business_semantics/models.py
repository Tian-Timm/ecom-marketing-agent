"""Versioned source configuration and canonical e-commerce domain objects."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping

from .errors import SourceConfigValidationError


SOURCE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


class SourceStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    INVALID = "INVALID"
    DISABLED = "DISABLED"


class ProductReferenceStrategy(str, Enum):
    """How a task resolves its product_id from the mapped task field."""

    AUTO = "AUTO"
    DIRECT_VALUE = "DIRECT_VALUE"
    LINKED_RECORD = "LINKED_RECORD"


@dataclass(frozen=True)
class FieldMapping:
    """A confirmed mapping from one physical connector field to one meaning."""

    field_id: str
    last_known_name: str
    source_type: str
    required: bool = False
    transform: str | Mapping[str, Any] | None = None

    def validate(self, *, location: str) -> None:
        if not self.field_id.strip():
            raise SourceConfigValidationError("字段映射缺少 field_id", location=location)
        if not self.last_known_name.strip():
            raise SourceConfigValidationError("字段映射缺少最后确认的字段名", location=location)
        if not self.source_type.strip():
            raise SourceConfigValidationError("字段映射缺少来源字段类型", location=location)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "FieldMapping":
        return cls(
            field_id=str(raw.get("field_id") or ""),
            last_known_name=str(raw.get("last_known_name") or ""),
            source_type=str(raw.get("source_type") or ""),
            required=bool(raw.get("required", False)),
            transform=raw.get("transform"),
        )


@dataclass(frozen=True)
class ConnectorConfig:
    """Connection location only; credentials are referenced, never embedded."""

    type: str
    base_token: str
    credential_ref: str

    def validate(self) -> None:
        if self.type != "feishu_base":
            raise SourceConfigValidationError("当前仅支持 feishu_base 连接器", connector_type=self.type)
        if not self.base_token.strip():
            raise SourceConfigValidationError("连接器缺少 base_token")
        if not self.credential_ref.strip():
            raise SourceConfigValidationError("连接器必须使用 credential_ref 引用凭证")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ConnectorConfig":
        forbidden = [key for key in raw if "secret" in key.lower() or "password" in key.lower()]
        if forbidden:
            raise SourceConfigValidationError("配置不得包含明文密钥", fields=forbidden)
        return cls(
            type=str(raw.get("type") or ""),
            base_token=str(raw.get("base_token") or ""),
            credential_ref=str(raw.get("credential_ref") or ""),
        )


@dataclass(frozen=True)
class TableMapping:
    table_id: str
    primary_key: str
    fields: Mapping[str, FieldMapping]

    def validate(self, *, role: str, required_fields: set[str]) -> None:
        if not self.table_id.strip():
            raise SourceConfigValidationError("表定义缺少 table_id", table=role)
        if self.primary_key not in self.fields:
            raise SourceConfigValidationError(
                "表主键必须映射为字段", table=role, primary_key=self.primary_key
            )
        missing = sorted(required_fields - set(self.fields))
        if missing:
            raise SourceConfigValidationError("表缺少必填业务字段映射", table=role, fields=missing)
        physical_fields: dict[str, str] = {}
        for semantic_name, mapping in self.fields.items():
            mapping.validate(location=f"tables.{role}.fields.{semantic_name}")
            previous = physical_fields.setdefault(mapping.field_id, semantic_name)
            if previous != semantic_name:
                raise SourceConfigValidationError(
                    "同一物理字段不能映射为多个业务语义",
                    table=role,
                    field_id=mapping.field_id,
                    semantics=[previous, semantic_name],
                )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TableMapping":
        fields_raw = raw.get("fields") or {}
        if not isinstance(fields_raw, Mapping):
            raise SourceConfigValidationError("表 fields 必须是对象")
        return cls(
            table_id=str(raw.get("table_id") or ""),
            primary_key=str(raw.get("primary_key") or ""),
            fields={str(name): FieldMapping.from_dict(value) for name, value in fields_raw.items()},
        )


@dataclass(frozen=True)
class ProductReference:
    """The product reference remains configurable until a source is confirmed."""

    strategy: ProductReferenceStrategy = ProductReferenceStrategy.AUTO
    task_field: str = "product_id"

    def validate(self, task_fields: Mapping[str, FieldMapping]) -> None:
        mapping = task_fields.get(self.task_field)
        if mapping is None:
            raise SourceConfigValidationError(
                "商品关联字段必须映射到任务表", task_field=self.task_field
            )
        field_type = normalize_source_type(mapping.source_type)
        if self.strategy == ProductReferenceStrategy.LINKED_RECORD and field_type != "linked_record":
            raise SourceConfigValidationError(
                "关联记录策略要求 linked_record 字段", field_type=mapping.source_type
            )
        if self.strategy == ProductReferenceStrategy.DIRECT_VALUE and field_type not in {"text", "number"}:
            raise SourceConfigValidationError(
                "直接商品编号策略只接受 text 或 number 字段", field_type=mapping.source_type
            )
        if self.strategy == ProductReferenceStrategy.AUTO and field_type not in {"text", "number", "linked_record"}:
            raise SourceConfigValidationError(
                "自动商品关联仅支持 text、number 或 linked_record 字段", field_type=mapping.source_type
            )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "ProductReference":
        raw = raw or {}
        try:
            strategy = ProductReferenceStrategy(str(raw.get("strategy") or "AUTO"))
        except ValueError as exc:
            raise SourceConfigValidationError("商品关联策略无效", strategy=raw.get("strategy")) from exc
        return cls(strategy=strategy, task_field=str(raw.get("task_field") or "product_id"))


def normalize_source_type(source_type: str) -> str:
    """Normalize stable Feishu and CLI field type spellings used by configs."""
    normalized = str(source_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "1": "text", "text": "text", "single_text": "text", "multiline_text": "text",
        "2": "number", "number": "number", "numeric": "number",
        "5": "date", "date": "date", "datetime": "date",
        "18": "linked_record", "link": "linked_record", "single_link": "linked_record",
        "linked_record": "linked_record", "record_link": "linked_record",
        "attachment": "attachment", "17": "attachment",
        "3": "single_select", "single_select": "single_select", "select": "single_select",
        "4": "multi_select", "multi_select": "multi_select",
    }
    return aliases.get(normalized, normalized)


@dataclass(frozen=True)
class DataSourceConfig:
    """A user-confirmed configuration revision for a source's business meaning."""

    source_id: str
    display_name: str
    connector: ConnectorConfig
    revision: int
    status: SourceStatus
    schema_fingerprint: str
    tables: Mapping[str, TableMapping]
    writeback: Mapping[str, FieldMapping]
    product_reference: ProductReference = field(default_factory=ProductReference)
    schema_version: str = "1.0"

    def validate(self) -> None:
        if not SOURCE_ID_PATTERN.fullmatch(self.source_id):
            raise SourceConfigValidationError("source_id 必须是 2-64 位小写业务标识", source_id=self.source_id)
        if not self.display_name.strip():
            raise SourceConfigValidationError("数据源缺少 display_name", source_id=self.source_id)
        if self.revision < 1:
            raise SourceConfigValidationError("配置 revision 必须从 1 开始", revision=self.revision)
        if not isinstance(self.status, SourceStatus):
            raise SourceConfigValidationError("数据源状态无效", status=str(self.status))
        if not self.schema_fingerprint.strip():
            raise SourceConfigValidationError("配置缺少 schema_fingerprint")
        self.connector.validate()

        expected_tables = {"products", "tasks"}
        missing_tables = sorted(expected_tables - set(self.tables))
        if missing_tables:
            raise SourceConfigValidationError("配置缺少业务表定义", tables=missing_tables)
        self.tables["products"].validate(role="products", required_fields={
            "product_id", "product_name", "min_price",
        })
        self.tables["tasks"].validate(
            role="tasks", required_fields={
                "task_id", "product_id", "img_type", "aspect_ratio", "main_text",
                "promo_price", "deploy_date",
            }
        )
        if self.tables["products"].primary_key != "product_id":
            raise SourceConfigValidationError("商品表主键必须为 product_id")
        if self.tables["tasks"].primary_key != "task_id":
            raise SourceConfigValidationError("任务表主键必须为 task_id")
        self.product_reference.validate(self.tables["tasks"].fields)

        task_inputs = self.tables["tasks"].fields
        task_physical_ids = {mapping.field_id for mapping in task_inputs.values()}
        writeback_ids: dict[str, str] = {}
        for semantic_name, mapping in self.writeback.items():
            mapping.validate(location=f"writeback.{semantic_name}")
            previous = writeback_ids.setdefault(mapping.field_id, semantic_name)
            if previous != semantic_name:
                raise SourceConfigValidationError(
                    "同一回写字段不能承载多个输出语义",
                    field_id=mapping.field_id,
                    semantics=[previous, semantic_name],
                )
            if mapping.field_id in task_physical_ids:
                raise SourceConfigValidationError(
                    "输入字段与回写字段必须分离",
                    field_id=mapping.field_id,
                    writeback_field=semantic_name,
                )

    def require_active(self) -> None:
        if self.status != SourceStatus.ACTIVE:
            from .errors import SourceConfigInactiveError

            raise SourceConfigInactiveError(self.source_id, self.status.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_id": self.source_id,
            "display_name": self.display_name,
            "connector": asdict(self.connector),
            "revision": self.revision,
            "status": self.status.value,
            "schema_fingerprint": self.schema_fingerprint,
            "tables": {
                name: {
                    "table_id": table.table_id,
                    "primary_key": table.primary_key,
                    "fields": {field_name: asdict(mapping) for field_name, mapping in table.fields.items()},
                }
                for name, table in self.tables.items()
            },
            "writeback": {name: asdict(mapping) for name, mapping in self.writeback.items()},
            "product_reference": {
                "strategy": self.product_reference.strategy.value,
                "task_field": self.product_reference.task_field,
            },
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "DataSourceConfig":
        tables_raw = raw.get("tables") or {}
        writeback_raw = raw.get("writeback") or {}
        if not isinstance(tables_raw, Mapping) or not isinstance(writeback_raw, Mapping):
            raise SourceConfigValidationError("tables 与 writeback 必须是对象")
        try:
            status = SourceStatus(str(raw.get("status") or ""))
        except ValueError as exc:
            raise SourceConfigValidationError("数据源状态无效", status=raw.get("status")) from exc
        config = cls(
            source_id=str(raw.get("source_id") or ""),
            display_name=str(raw.get("display_name") or ""),
            connector=ConnectorConfig.from_dict(raw.get("connector") or {}),
            revision=int(raw.get("revision") or 0),
            status=status,
            schema_fingerprint=str(raw.get("schema_fingerprint") or ""),
            tables={str(name): TableMapping.from_dict(value) for name, value in tables_raw.items()},
            writeback={str(name): FieldMapping.from_dict(value) for name, value in writeback_raw.items()},
            product_reference=ProductReference.from_dict(raw.get("product_reference")),
            schema_version=str(raw.get("schema_version") or "1.0"),
        )
        config.validate()
        return config


@dataclass(frozen=True)
class ProductKey:
    source_id: str
    product_id: str

    @property
    def value(self) -> str:
        return f"{self.source_id}:{self.product_id}"


@dataclass(frozen=True)
class TaskKey:
    source_id: str
    task_id: str

    @property
    def value(self) -> str:
        return f"{self.source_id}:{self.task_id}"


@dataclass(frozen=True)
class StandardProduct:
    product_id: str
    product_name: str
    min_price: float | None = None
    sku: str | None = None
    regular_price: float | None = None
    selling_points: str | None = None
    product_image: Any | None = None
    logo_image: Any | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def key_for(self, source_id: str) -> ProductKey:
        return ProductKey(source_id, self.product_id)


@dataclass(frozen=True)
class StandardMarketingTask:
    task_id: str
    product_id: str
    img_type: str
    aspect_ratio: str
    deploy_date: str
    main_text: str
    sub_text: str = ""
    campaign_name: str | None = None
    campaign_start: str | None = None
    campaign_end: str | None = None
    promo_price: float | None = None
    # Optional so already-active source configurations remain valid.  The
    # renderer resolves an empty value to the published built-in template.
    template_id: str | None = None

    def key_for(self, source_id: str) -> TaskKey:
        return TaskKey(source_id, self.task_id)


@dataclass(frozen=True)
class SourceReceipt:
    source_id: str
    config_revision: int
    product_table_id: str
    product_record_id: str
    task_table_id: str
    task_record_id: str


@dataclass(frozen=True)
class PreparedTask:
    source_id: str
    config_revision: int
    task: StandardMarketingTask
    product: StandardProduct
    receipt: SourceReceipt
    schema_fingerprint: str
    input_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.source_id != self.receipt.source_id:
            raise ValueError("PreparedTask 的 source_id 必须与 SourceReceipt 一致")
        if self.config_revision != self.receipt.config_revision:
            raise ValueError("PreparedTask 的配置版本必须与 SourceReceipt 一致")
        if self.task.product_id != self.product.product_id:
            raise ValueError("PreparedTask 的任务 product_id 必须匹配商品 product_id")

    @property
    def task_key(self) -> TaskKey:
        return self.task.key_for(self.source_id)

    @property
    def product_key(self) -> ProductKey:
        return self.product.key_for(self.source_id)
