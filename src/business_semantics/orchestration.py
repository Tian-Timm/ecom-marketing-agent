"""Configuration-driven compliance, rendering and precise delivery orchestration."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .discovery import normalize_field_metadata, normalize_sample_records
from .errors import SourceConfigValidationError
from .models import PreparedTask
from .repository import SourceConfigRepository
from .runtime import ConfiguredSourceRuntime, FeishuDataGateway


TERMINAL_BASE_STATUSES = {"审查通过", "需修改", "待人工复核", "执行失败"}
STATUS_TO_BASE = {"PASSED": "审查通过", "BLOCKED": "需修改", "REVIEW_REQUIRED": "待人工复核", "FAILED": "执行失败"}
BASE_TO_STATUS = {value: key for key, value in STATUS_TO_BASE.items()}
REQUIRED_WRITEBACK = {"status", "issues", "processed_at", "input_hash", "pipeline_version"}
_AUTO_REVIEWER = object()


class DeliveryGateway(FeishuDataGateway, Protocol):
    def upload_image_for_base(self, file_path: Path, task_id: str, base_token: str) -> Mapping[str, Any]: ...

    def batch_update(self, base_token: str, table_id: str, updates: Mapping[str, Mapping[str, Any]]) -> None: ...


PipelineExecutor = Callable[..., dict[str, Any]]


def _existing_executor(record: dict[str, Any], output_dir: Path, *, source: str, reviewer: Any) -> dict[str, Any]:
    scripts = Path(__file__).resolve().parents[2] / ".agents" / "skills" / "simple-visual-compliance" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from audit_text import load_rules
    from run_pipeline import execute_task
    kwargs = {} if reviewer is _AUTO_REVIEWER else {"semantic_reviewer": reviewer}
    return execute_task(record, output_dir, rules=load_rules(), source=source, **kwargs)


def _default_versions() -> tuple[str, str]:
    scripts = Path(__file__).resolve().parents[2] / ".agents" / "skills" / "simple-visual-compliance" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from audit_text import load_rules
    from run_pipeline import PIPELINE_VERSION
    return str(load_rules().get("version", "unknown")), str(PIPELINE_VERSION)


def _scalar(value: Any) -> str:
    if isinstance(value, list):
        return _scalar(value[0]) if len(value) == 1 else ""
    if isinstance(value, Mapping):
        return str(value.get("text") or value.get("name") or value.get("value") or "")
    return str(value or "")


def _execution_fingerprint(prepared: PreparedTask, *, rules_version: str, template_version: str) -> str:
    payload = {
        "prepared_input_fingerprint": prepared.input_fingerprint,
        "rules_version": rules_version,
        "template_version": template_version,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _canonical_record(prepared: PreparedTask, material_paths: Mapping[str, str]) -> dict[str, Any]:
    task, product = prepared.task, prepared.product
    return {
        "task_id": task.task_id, "product_id": product.product_id, "product_name": product.product_name,
        "min_price": product.min_price, "img_type": task.img_type, "aspect_ratio": task.aspect_ratio,
        "deploy_date": task.deploy_date, "campaign_name": task.campaign_name or "",
        "campaign_start": task.campaign_start or "", "campaign_end": task.campaign_end or "",
        "promo_price": task.promo_price, "main_text": task.main_text, "sub_text": task.sub_text,
        **material_paths,
    }


class ConfiguredSourceOrchestrator:
    """One interface hides preparation, idempotency, execution, upload and exact writeback."""

    def __init__(
        self,
        repository: SourceConfigRepository,
        gateway: DeliveryGateway,
        *,
        output_root: Path,
        pipeline_executor: PipelineExecutor | None = None,
        semantic_reviewer: Any = _AUTO_REVIEWER,
        rules_version: str | None = None,
        template_version: str | None = None,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._runtime = ConfiguredSourceRuntime(repository, gateway)
        self._output_root = output_root
        self._executor = pipeline_executor or _existing_executor
        self._reviewer = semantic_reviewer
        default_rules, default_template = _default_versions() if pipeline_executor is None and (rules_version is None or template_version is None) else ("injected", "injected")
        self._rules_version = rules_version or default_rules
        self._template_version = template_version or default_template

    def _writeback_metadata(self, prepared: PreparedTask) -> tuple[dict[str, str], Mapping[str, Any]]:
        config = self._repository.get_active(prepared.source_id)
        missing = REQUIRED_WRITEBACK - set(config.writeback)
        if missing:
            raise SourceConfigValidationError("正式交付缺少必填回写字段", fields=sorted(missing))
        if not ({"image_attachment", "image_url"} & set(config.writeback)):
            raise SourceConfigValidationError("正式交付必须配置图片附件或图片链接回写字段")
        metadata = {field.field_id: field for field in normalize_field_metadata(
            self._gateway.list_fields(config.connector.base_token, prepared.receipt.task_table_id)
        )}
        names: dict[str, str] = {}
        for semantic, mapping in config.writeback.items():
            field = metadata.get(mapping.field_id)
            if field is None:
                raise SourceConfigValidationError("回写字段在当前 Base 中不存在", field_id=mapping.field_id)
            names[semantic] = field.name
        records = normalize_sample_records(self._gateway.list_records(config.connector.base_token, prepared.receipt.task_table_id))
        record = next((item for item in records if item.record_id == prepared.receipt.task_record_id), None)
        if record is None:
            raise SourceConfigValidationError("回写目标任务记录不存在", record_id=prepared.receipt.task_record_id)
        return names, record.fields

    def _material_paths(self, prepared: PreparedTask, source_dir: Path) -> dict[str, str]:
        config = self._repository.get_active(prepared.source_id)
        paths: dict[str, str] = {}
        for semantic, value, target_name in (
            ("product_image", prepared.product.product_image, "product_image_path"),
            ("logo_image", prepared.product.logo_image, "logo_image_path"),
        ):
            if semantic not in config.tables["products"].fields:
                continue
            if value is None or not hasattr(self._gateway, "download_media"):
                raise SourceConfigValidationError("已配置的商品素材无法读取，拒绝使用默认素材", semantic=semantic)
            token = ""
            if isinstance(value, list) and value and isinstance(value[0], Mapping):
                token = str(value[0].get("file_token") or value[0].get("token") or "")
            if isinstance(value, Mapping):
                token = str(value.get("file_token") or value.get("token") or token)
            if not token:
                raise SourceConfigValidationError("已配置的商品素材缺少 file_token", semantic=semantic)
            content, _ = self._gateway.download_media(token)
            material_dir = source_dir / "materials"
            material_dir.mkdir(parents=True, exist_ok=True)
            path = material_dir / f"{prepared.receipt.product_record_id}_{semantic}.png"
            path.write_bytes(content)
            paths[target_name] = str(path)
        return paths

    def run_task(self, source_id: str, task_id: str, *, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
        prepared = self._runtime.prepare_task(source_id, task_id)
        config = self._repository.get_active(source_id)
        writeback_names, existing_fields = self._writeback_metadata(prepared)
        execution_hash = _execution_fingerprint(prepared, rules_version=self._rules_version, template_version=self._template_version)
        existing_hash = _scalar(existing_fields.get(writeback_names["input_hash"]))
        existing_status = _scalar(existing_fields.get(writeback_names["status"]))
        if not force and existing_hash == execution_hash and existing_status in TERMINAL_BASE_STATUSES:
            status = BASE_TO_STATUS[existing_status]
            return {
                "status": status, "sync_status": "SKIPPED_UNCHANGED", "task_id": prepared.task.task_id,
                "source_id": source_id, "product_id": prepared.product.product_id, "config_revision": prepared.config_revision,
                "schema_fingerprint": prepared.schema_fingerprint, "input_hash": execution_hash,
                "receipt": prepared.receipt, "issues": existing_fields.get(writeback_names["issues"]),
                "processed_at": existing_fields.get(writeback_names["processed_at"]),
                "pipeline_version": existing_fields.get(writeback_names["pipeline_version"]),
                "image_attachment": existing_fields.get(writeback_names["image_attachment"]) if "image_attachment" in writeback_names else None,
                "image_url": existing_fields.get(writeback_names["image_url"]) if "image_url" in writeback_names else None,
                "generated_image": existing_fields.get(writeback_names["image_url"]) if "image_url" in writeback_names else existing_fields.get(writeback_names["image_attachment"]) if "image_attachment" in writeback_names else None,
            }

        # PreparedTask.source_id comes from validated configuration, not request text.
        source_dir = self._output_root / prepared.source_id
        source_dir.mkdir(parents=True, exist_ok=True)
        try:
            material_paths = self._material_paths(prepared, source_dir)
            result = self._executor(
                _canonical_record(prepared, material_paths), source_dir,
                source=source_id, reviewer=self._reviewer,
            )
        except Exception as exc:
            result = {"status": "FAILED", "generated_image": None, "violations": [], "error": {"code": "ORCHESTRATION_EXECUTION_FAILED", "message": str(exc)}}

        result = dict(result)
        result.update({
            "source_id": source_id, "product_id": prepared.product.product_id,
            "config_revision": prepared.config_revision, "schema_fingerprint": prepared.schema_fingerprint,
            "task_record_id": prepared.receipt.task_record_id, "product_record_id": prepared.receipt.product_record_id,
            "input_hash": execution_hash, "prepared_input_fingerprint": prepared.input_fingerprint,
            "rules_version": result.get("rules_version", self._rules_version),
            "pipeline_version": result.get("pipeline_version", self._template_version),
        })
        if result.get("status") != "PASSED":
            result["generated_image"] = None
        if dry_run:
            result["sync_status"] = "DRY_RUN"
            return result

        image_url = None
        attachment = None
        if result.get("status") == "PASSED":
            try:
                uploaded = self._gateway.upload_image_for_base(
                    source_dir / str(result["generated_image"]), prepared.task.task_id, config.connector.base_token,
                )
                image_url = uploaded.get("url")
                attachment = uploaded.get("attachment")
                result["image_attachment"] = attachment
                result["image_file_token"] = next((str(item.get("file_token") or "") for item in attachment or [] if isinstance(item, Mapping)), "") or None
            except Exception as exc:
                result.update({"status": "FAILED", "generated_image": None, "error": {"code": "FEISHU_UPLOAD_FAILED", "message": str(exc)}})

        issues = "；".join(str(item.get("message") or "") for item in result.get("violations", []) if item.get("message"))
        if result.get("status") == "FAILED":
            issues = str((result.get("error") or {}).get("message") or issues or "执行失败")
        now = datetime.now(timezone.utc)
        fields: dict[str, Any] = {
            writeback_names["status"]: STATUS_TO_BASE.get(result.get("status"), "执行失败"),
            writeback_names["issues"]: issues or None,
            writeback_names["processed_at"]: int(now.timestamp() * 1000),
            writeback_names["input_hash"]: execution_hash,
            writeback_names["pipeline_version"]: f"规则 {result['rules_version']} / 流水线 {result['pipeline_version']}",
        }
        if "image_attachment" in writeback_names:
            fields[writeback_names["image_attachment"]] = attachment if result.get("status") == "PASSED" else None
        if "image_url" in writeback_names:
            fields[writeback_names["image_url"]] = image_url if result.get("status") == "PASSED" else None
        try:
            self._gateway.batch_update(config.connector.base_token, prepared.receipt.task_table_id, {prepared.receipt.task_record_id: fields})
            result["sync_status"] = "COMPLETED"
        except Exception as exc:
            result.update({"status": "FAILED", "sync_status": "WRITEBACK_FAILED", "generated_image": None, "error": {"code": "FEISHU_WRITEBACK_FAILED", "message": str(exc)}})
        return result
