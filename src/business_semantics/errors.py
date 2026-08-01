"""Structured failures exposed by the business semantics boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class SemanticLayerError(Exception):
    """A stable error suitable for APIs, logs, and future UI presentation."""

    code: str
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


class SourceConfigValidationError(SemanticLayerError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__("SOURCE_CONFIG_INVALID", message, details)


class SourceConfigNotFoundError(SemanticLayerError):
    def __init__(self, source_id: str, revision: int | None = None) -> None:
        detail = {"source_id": source_id}
        if revision is not None:
            detail["revision"] = revision
        super().__init__("SOURCE_CONFIG_NOT_FOUND", "未找到数据源配置", detail)


class SourceConfigRevisionConflictError(SemanticLayerError):
    def __init__(self, source_id: str, message: str, **details: Any) -> None:
        super().__init__(
            "SOURCE_CONFIG_REVISION_CONFLICT",
            message,
            {"source_id": source_id, **details},
        )


class SourceConfigInactiveError(SemanticLayerError):
    def __init__(self, source_id: str, status: str) -> None:
        super().__init__(
            "SOURCE_INACTIVE",
            "数据源尚未激活，不能进入正式运行",
            {"source_id": source_id, "status": status},
        )


class SourceSchemaDriftError(SemanticLayerError):
    def __init__(self, source_id: str, issues: list[Mapping[str, Any]]) -> None:
        super().__init__(
            "SOURCE_SCHEMA_DRIFTED",
            "数据源结构已发生破坏性变化，禁止进入正式运行",
            {"source_id": source_id, "issues": issues},
        )


class SourceDryRunError(SemanticLayerError):
    def __init__(self, source_id: str, issues: list[Mapping[str, Any]]) -> None:
        super().__init__(
            "SOURCE_DRY_RUN_FAILED",
            "数据源样本验证未通过，配置尚未保存",
            {"source_id": source_id, "issues": issues},
        )


class SourceConfigUnverifiedError(SemanticLayerError):
    def __init__(self, source_id: str, revision: int) -> None:
        super().__init__(
            "SOURCE_CONFIG_UNVERIFIED",
            "配置尚未通过确认时的只读样本验证，不能激活",
            {"source_id": source_id, "revision": revision},
        )


class SourceRecordLookupError(SemanticLayerError):
    def __init__(
        self, code: str, source_id: str, identifier: str, value: str, count: int
    ) -> None:
        super().__init__(
            code,
            "未找到唯一的源记录" if count != 1 else "源记录查询失败",
            {
                "source_id": source_id,
                "identifier": identifier,
                "value": value,
                "count": count,
            },
        )


class SourceValueConversionError(SemanticLayerError):
    def __init__(self, source_id: str, semantic: str, message: str, **details: Any) -> None:
        super().__init__(
            "VALUE_CONVERSION_FAILED", message,
            {"source_id": source_id, "semantic": semantic, **details},
        )
