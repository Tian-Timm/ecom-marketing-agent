"""Storage seam for versioned source configurations."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Protocol

from .errors import (
    SourceConfigNotFoundError,
    SourceConfigRevisionConflictError,
    SourceConfigUnverifiedError,
    SourceConfigValidationError,
)
from .models import DataSourceConfig, SourceStatus


class SourceConfigRepository(Protocol):
    """Persistence boundary; production can replace this with a database adapter."""

    def save(
        self,
        config: DataSourceConfig,
        *,
        expected_active_revision: int | None = None,
    ) -> None: ...

    def get(self, source_id: str, revision: int) -> DataSourceConfig: ...

    def get_active(self, source_id: str) -> DataSourceConfig: ...

    def mark_dry_run_verified(
        self, source_id: str, revision: int, *, evidence: Mapping[str, Any]
    ) -> None: ...

    def activate(self, source_id: str, revision: int, *, expected_active_revision: int | None) -> None: ...


class LocalFileSourceConfigRepository:
    """JSON-file adapter for development, tests, and local confirmed configurations.

    Each revision is append-only. Activation changes only a small pointer file and
    requires a compare-and-set revision once a source already has an active one.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _source_dir(self, source_id: str) -> Path:
        return self.root / source_id

    def _revision_path(self, source_id: str, revision: int) -> Path:
        return self._source_dir(source_id) / f"revision-{revision}.json"

    def _active_path(self, source_id: str) -> Path:
        return self._source_dir(source_id) / "active.json"

    def _verification_path(self, source_id: str, revision: int) -> Path:
        return self._source_dir(source_id) / f"revision-{revision}.verification.json"

    @staticmethod
    def _append_json(path: Path, payload: dict) -> None:
        """Create immutable revision/proof evidence; existing targets always conflict."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise SourceConfigRevisionConflictError(
                path.parent.name, "不可变配置证据已存在，禁止覆盖", path=str(path)
            )
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
            os.replace(temporary, path)
        except FileExistsError:
            # A stale interrupted write must never be silently replaced.
            raise SourceConfigRevisionConflictError(
                path.parent.name, "存在未完成的配置写入，请人工确认后重试", path=str(temporary)
            )

    @staticmethod
    def _replace_active_pointer(path: Path, payload: dict) -> None:
        """Only the active pointer is mutable, after the caller's CAS checks."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("x", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
            os.replace(temporary, path)
        except FileExistsError:
            raise SourceConfigRevisionConflictError(
                path.parent.name, "存在未完成的激活指针写入，请人工确认后重试", path=str(temporary)
            )

    def _active_revision(self, source_id: str) -> int | None:
        path = self._active_path(source_id)
        if not path.exists():
            return None
        try:
            return int(json.loads(path.read_text(encoding="utf-8"))["revision"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SourceConfigRevisionConflictError(
                source_id, "激活指针损坏，拒绝覆盖", path=str(path)
            ) from exc

    def save(
        self,
        config: DataSourceConfig,
        *,
        expected_active_revision: int | None = None,
    ) -> None:
        config.validate()
        if config.status != SourceStatus.DRAFT:
            raise SourceConfigValidationError("新配置 revision 必须以 DRAFT 状态保存")
        actual_active = self._active_revision(config.source_id)
        if expected_active_revision is not None and actual_active != expected_active_revision:
            raise SourceConfigRevisionConflictError(
                config.source_id,
                "当前激活版本已变化，拒绝保存过期配置",
                expected_active_revision=expected_active_revision,
                actual_active_revision=actual_active,
            )
        if actual_active is not None and config.revision <= actual_active:
            raise SourceConfigRevisionConflictError(
                config.source_id,
                "新配置版本必须大于当前激活版本",
                revision=config.revision,
                active_revision=actual_active,
            )
        destination = self._revision_path(config.source_id, config.revision)
        if destination.exists():
            raise SourceConfigRevisionConflictError(
                config.source_id, "配置 revision 已存在，禁止静默覆盖", revision=config.revision
            )
        self._append_json(destination, config.to_dict())

    def mark_dry_run_verified(
        self,
        source_id: str,
        revision: int,
        *,
        evidence: Mapping[str, Any],
    ) -> None:
        """Append immutable confirmation evidence after a successful read-only dry-run."""
        config = self.get(source_id, revision)
        if config.status != SourceStatus.DRAFT:
            raise SourceConfigValidationError("只有 DRAFT 配置可以记录确认验证", revision=revision)
        self._append_json(self._verification_path(source_id, revision), {
            "source_id": source_id,
            "revision": revision,
            "schema_fingerprint": config.schema_fingerprint,
            "status": "PASSED",
            "evidence": dict(evidence),
        })

    def _verified(self, source_id: str, revision: int, config: DataSourceConfig) -> bool:
        path = self._verification_path(source_id, revision)
        if not path.exists():
            return False
        try:
            proof = json.loads(path.read_text(encoding="utf-8"))
            return (
                proof.get("status") == "PASSED"
                and proof.get("source_id") == source_id
                and int(proof.get("revision")) == revision
                and proof.get("schema_fingerprint") == config.schema_fingerprint
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return False

    def get(self, source_id: str, revision: int) -> DataSourceConfig:
        path = self._revision_path(source_id, revision)
        if not path.exists():
            raise SourceConfigNotFoundError(source_id, revision)
        return DataSourceConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def get_active(self, source_id: str) -> DataSourceConfig:
        revision = self._active_revision(source_id)
        if revision is None:
            raise SourceConfigNotFoundError(source_id)
        config = self.get(source_id, revision)
        if not self._verified(source_id, revision, config):
            raise SourceConfigUnverifiedError(source_id, revision)
        # Revision JSON is immutable DRAFT evidence; the active pointer supplies
        # the effective runtime status without rewriting that historical record.
        return replace(config, status=SourceStatus.ACTIVE)

    def activate(
        self,
        source_id: str,
        revision: int,
        *,
        expected_active_revision: int | None,
    ) -> None:
        config = self.get(source_id, revision)
        if config.status != SourceStatus.DRAFT:
            raise SourceConfigValidationError("只能激活经过确认的 DRAFT revision", revision=revision)
        if not self._verified(source_id, revision, config):
            raise SourceConfigUnverifiedError(source_id, revision)
        actual_active = self._active_revision(source_id)
        if actual_active is not None and expected_active_revision is None:
            raise SourceConfigRevisionConflictError(
                source_id, "切换激活配置必须提供 expected_active_revision", actual_active_revision=actual_active
            )
        if actual_active != expected_active_revision:
            raise SourceConfigRevisionConflictError(
                source_id,
                "当前激活版本已变化，拒绝切换配置",
                expected_active_revision=expected_active_revision,
                actual_active_revision=actual_active,
            )
        self._replace_active_pointer(self._active_path(source_id), {"revision": revision})

    def list_source_ids(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(path.name for path in self.root.iterdir() if path.is_dir())
