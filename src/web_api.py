"""Vercel 函数共用的安全请求处理入口。"""

from __future__ import annotations

import hmac
import json
import os
import sys
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = (
    PROJECT_ROOT
    / ".agents"
    / "skills"
    / "simple-visual-compliance"
    / "scripts"
)
STANDARDIZER_DIR = (
    PROJECT_ROOT
    / ".agents"
    / "skills"
    / "dataset-standardizer"
    / "scripts"
)
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(STANDARDIZER_DIR))

from feishu_openapi_adapter import FeishuOpenApiAdapter
from online_runtime import (
    image_token_belongs_to_demo_base,
    live_report,
    run_protected_pending,
    run_protected_task,
    runtime_status,
)
from .business_semantics import (
    ConfiguredSourceOrchestrator, MappingSelection, ProductReferenceStrategy,
    SemanticLayerError, confirm_draft_config, discover_source,
)
from .source_composition import config_repository, resolve_feishu_credential

MAX_BODY_BYTES = 8 * 1024
RATE_WINDOW_SECONDS = 10 * 60
RATE_LIMIT = 6
PUBLIC_DEMO_RATE_WINDOW_DEFAULT = 600
PUBLIC_DEMO_RATE_LIMIT_DEFAULT = 3
FORBIDDEN_PUBLIC_KEYS = {
    "source_id", "base_token", "base_url", "table_id",
    "record_id", "credential_ref", "force",
}
TERMINAL_BASE_STATUSES = {"审查通过", "需修改", "待人工复核", "执行失败"}
_state_lock = threading.Lock()
_requests_by_ip: dict[str, list[float]] = {}
_public_demo_requests_by_ip: dict[str, list[float]] = {}
_running_tasks: set[str] = set()


class RequestConflictError(RuntimeError):
    """A client-request conflict that is safe to return as HTTP 409."""


class RateLimitExceededError(RuntimeError):
    """Public demo rate limit exceeded that should return HTTP 429."""

    def __init__(self, retry_after: int = 600) -> None:
        super().__init__("操作过于频繁，请稍后再试")
        self.retry_after = retry_after


class PublicDemoDisabledError(RuntimeError):
    """Public demo is disabled on this instance."""

    def __init__(self, message: str = "实时演示暂时不可用，可查看已有结果") -> None:
        super().__init__(message)


def send_json(
    handler: Any,
    payload: Any,
    status: HTTPStatus = HTTPStatus.OK,
    headers: dict[str, str] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    if headers:
        for k, v in headers.items():
            handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: Any) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0 or length > MAX_BODY_BYTES:
        raise ValueError("请求内容为空或超过 8KB")
    payload = json.loads(handler.rfile.read(length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("请求内容必须是 JSON 对象")
    return payload


def _client_ip(handler: Any) -> str:
    forwarded = handler.headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[0].strip() or str(handler.client_address[0])


def _authorize_admin(handler: Any) -> None:
    expected = os.environ.get("DEMO_ADMIN_TOKEN", "").strip()
    supplied = handler.headers.get("X-Demo-Admin-Token", "").strip()
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise PermissionError("执行口令无效")


def _authorize_write(handler: Any) -> None:
    """Administrator-only mutation guard; GET reads use _authorize_admin only."""
    _authorize_admin(handler)
    now = time.monotonic()
    client_ip = _client_ip(handler)
    with _state_lock:
        recent = [
            timestamp
            for timestamp in _requests_by_ip.get(client_ip, [])
            if now - timestamp < RATE_WINDOW_SECONDS
        ]
        if len(recent) >= RATE_LIMIT:
            raise RuntimeError("操作过于频繁，请稍后再试")
        recent.append(now)
        _requests_by_ip[client_ip] = recent


def _is_public_demo_enabled() -> bool:
    return os.environ.get("PUBLIC_DEMO_RUN_ENABLED", "").strip().lower() in ("true", "1")


def _authorize_public_demo(handler: Any) -> None:
    if not _is_public_demo_enabled():
        raise PublicDemoDisabledError()

    window = PUBLIC_DEMO_RATE_WINDOW_DEFAULT
    try:
        if "PUBLIC_DEMO_RATE_WINDOW_SECONDS" in os.environ:
            val = int(os.environ["PUBLIC_DEMO_RATE_WINDOW_SECONDS"])
            if val > 0:
                window = val
    except ValueError:
        pass

    limit = PUBLIC_DEMO_RATE_LIMIT_DEFAULT
    try:
        if "PUBLIC_DEMO_RATE_LIMIT" in os.environ:
            val = int(os.environ["PUBLIC_DEMO_RATE_LIMIT"])
            if val > 0:
                limit = val
    except ValueError:
        pass

    now = time.monotonic()
    client_ip = _client_ip(handler)
    with _state_lock:
        recent = [
            timestamp
            for timestamp in _public_demo_requests_by_ip.get(client_ip, [])
            if now - timestamp < window
        ]
        if len(recent) >= limit:
            oldest = recent[0]
            retry_after = max(1, int(window - (now - oldest)))
            raise RateLimitExceededError(retry_after=retry_after)
        recent.append(now)
        _public_demo_requests_by_ip[client_ip] = recent


def _get_public_demo_whitelist() -> set[str]:
    raw_env = os.environ.get("PUBLIC_DEMO_TASK_IDS", "").strip()
    if raw_env:
        return {item.strip() for item in raw_env.split(",") if item.strip()}

    snapshot_path = PROJECT_ROOT / "generated_output" / "pipeline_result.json"
    if snapshot_path.exists():
        try:
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            records = data.get("records", [])
            ids = {str(r.get("task_id") or "").strip() for r in records if isinstance(r, dict)}
            valid = {i for i in ids if i}
            if valid:
                return valid
        except Exception:
            pass

    demo_base_cfg = (
        PROJECT_ROOT
        / ".agents"
        / "skills"
        / "simple-visual-compliance"
        / "assets"
        / "feishu_demo_base.json"
    )
    if demo_base_cfg.exists():
        try:
            cfg = json.loads(demo_base_cfg.read_text(encoding="utf-8"))
            fixture_rel = cfg.get("legacy_input_fixture")
            if fixture_rel:
                fixture_path = PROJECT_ROOT / fixture_rel
                if fixture_path.exists():
                    from feishu_base_adapter import load_fixture_inputs
                    fixtures = load_fixture_inputs(fixture_path)
                    return {str(k).strip() for k in fixtures.keys() if str(k).strip()}
        except Exception:
            pass

    return set()


def _semantic_error(handler: Any, exc: Exception) -> None:
    """Return stable request errors without reflecting connector credentials."""
    if isinstance(exc, PermissionError):
        send_json(handler, {"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
        return
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        send_json(handler, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return
    if isinstance(exc, RequestConflictError):
        send_json(handler, {"error": str(exc)}, HTTPStatus.CONFLICT)
        return
    if isinstance(exc, RateLimitExceededError):
        send_json(
            handler,
            {"error": str(exc)},
            HTTPStatus.TOO_MANY_REQUESTS,
            headers={"Retry-After": str(exc.retry_after)},
        )
        return
    if isinstance(exc, PublicDemoDisabledError):
        send_json(handler, {"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
        return
    if isinstance(exc, KeyError):
        send_json(handler, {"error": "未找到指定演示任务"}, HTTPStatus.NOT_FOUND)
        return
    if isinstance(exc, SemanticLayerError):
        status = (
            HTTPStatus.NOT_FOUND if exc.code.endswith("NOT_FOUND")
            else HTTPStatus.CONFLICT if exc.code in {
                "SOURCE_CONFIG_REVISION_CONFLICT", "SOURCE_SCHEMA_DRIFTED",
                "SOURCE_CONFIG_UNVERIFIED", "SOURCE_INACTIVE",
            } or "DUPLICATE" in exc.code or "CONFLICT" in exc.code
            else HTTPStatus.BAD_REQUEST
        )
        send_json(handler, {"error": exc.as_dict()}, status)
    else:
        # Connector/network/credential failures may include sensitive upstream
        # diagnostics.  Their detail belongs in server logs, never in the API.
        send_json(handler, {"error": "服务暂不可用，请稍后重试"}, HTTPStatus.SERVICE_UNAVAILABLE)


def _scalar(value: Any) -> Any:
    """Normalize Feishu's single-value cells for dashboard summaries."""
    if isinstance(value, list):
        return _scalar(value[0]) if len(value) == 1 else value
    if isinstance(value, dict):
        for key in ("text", "name", "value", "record_id", "id"):
            if value.get(key) not in (None, ""):
                return _scalar(value[key])
    return value


def _selection_from_payload(payload: dict[str, Any]) -> MappingSelection:
    raw = payload.get("selection")
    if not isinstance(raw, dict):
        raise ValueError("selection 必须是对象")
    field_maps = {}
    for key in ("product_fields", "task_fields", "writeback_fields"):
        value = raw.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"selection.{key} 必须是对象")
        if any(not isinstance(name, str) or not isinstance(field_id, str) or not field_id.strip() for name, field_id in value.items()):
            raise ValueError(f"selection.{key} 含无效字段映射")
        field_maps[key] = dict(value)
    task_table_id = str(raw.get("task_table_id") or "")
    if raw.get("writeback_table_id") not in (None, "", task_table_id):
        raise ValueError("回写字段必须位于任务表")
    required = {"status", "issues", "processed_at", "input_hash", "pipeline_version"}
    missing = sorted(required - set(field_maps["writeback_fields"]))
    if missing:
        raise ValueError(f"回写映射缺少必填字段：{', '.join(missing)}")
    if not ({"image_attachment", "image_url"} & set(field_maps["writeback_fields"])):
        raise ValueError("回写映射必须至少包含 image_attachment 或 image_url")
    try:
        strategy = ProductReferenceStrategy(str(raw.get("product_reference_strategy") or "AUTO"))
    except ValueError as exc:
        raise ValueError("商品关联方式无效") from exc
    return MappingSelection(
        str(raw.get("product_table_id") or ""), task_table_id,
        field_maps["product_fields"], field_maps["task_fields"], field_maps["writeback_fields"], strategy,
    )


def _source_token(value: Any) -> str:
    text = str(value or "").strip()
    if "/base/" in text:
        text = text.rstrip("/").split("/base/")[-1].split("?")[0]
    if not text or len(text) > 128:
        raise ValueError("Base 标识无效")
    return text


def _source_context(source_id: str):
    repo, persistence = config_repository()
    if repo is None:
        raise RuntimeError("数据源配置存储不可用")
    config = repo.get_active(source_id)
    return repo, persistence, config, resolve_feishu_credential(config.connector.credential_ref)


def handle_get(handler: Any, resource: str) -> None:
    try:
        if resource == "status":
            status = runtime_status()
            _, persistence = config_repository()
            status["config_persistence"] = persistence.mode
            caps = status.setdefault("capabilities", {})
            caps["onboarding_write"] = persistence.mode == "persistent"
            public_demo_enabled = _is_public_demo_enabled()
            has_deps = bool(caps.get("feishu_read") and caps.get("semantic_review"))
            caps["public_demo_run"] = bool(public_demo_enabled and has_deps)
            send_json(handler, status)
        elif resource == "tasks":
            from urllib.parse import parse_qs, urlparse
            source_id = str((parse_qs(urlparse(handler.path).query).get("source_id") or [""])[0])
            if not source_id:
                send_json(handler, live_report())
                return
            _authorize_admin(handler)
            _, persistence, config, adapter = _source_context(source_id)
            table = config.tables["tasks"]
            names = {item["field_id"]: item["field_name"] for item in adapter.list_fields(config.connector.base_token, table.table_id)}
            input_names = {semantic: names[mapping.field_id] for semantic, mapping in table.fields.items()}
            output_names = {semantic: names[mapping.field_id] for semantic, mapping in config.writeback.items() if mapping.field_id in names}
            status_name = output_names.get("status")
            status_map = {"审查通过": "PASSED", "需修改": "BLOCKED", "待人工复核": "REVIEW_REQUIRED", "执行失败": "FAILED"}
            product_table = config.tables["products"]
            product_names = {item["field_id"]: item["field_name"] for item in adapter.list_fields(config.connector.base_token, product_table.table_id)}
            product_fields = {semantic: product_names[mapping.field_id] for semantic, mapping in product_table.fields.items()}
            products = {
                str(_scalar(row.get(product_fields["product_id"])) or "").strip(): row
                for row in adapter.list_records(config.connector.base_token, product_table.table_id)
            }
            records = []
            for record in adapter.list_records(config.connector.base_token, table.table_id):
                task_id = str(_scalar(record.get(input_names["task_id"])) or "").strip()
                if task_id:
                    product_id = _scalar(record.get(input_names.get("product_id")))
                    status = status_map.get(str(_scalar(record.get(status_name)) or ""), "PENDING")
                    image_attachment = record.get(output_names.get("image_attachment")) if output_names.get("image_attachment") else None
                    token = next((str(item.get("file_token") or "") for item in image_attachment or [] if isinstance(item, dict) and item.get("file_token")), "")
                    records.append({
                        "task_id": task_id, "product_id": product_id,
                        "img_type": _scalar(record.get(input_names.get("img_type"))), "aspect_ratio": _scalar(record.get(input_names.get("aspect_ratio"))),
                        "deploy_date": _scalar(record.get(input_names.get("deploy_date"))), "campaign_name": _scalar(record.get(input_names.get("campaign_name"))),
                        "campaign_start": _scalar(record.get(input_names.get("campaign_start"))), "campaign_end": _scalar(record.get(input_names.get("campaign_end"))),
                        "main_text": _scalar(record.get(input_names.get("main_text"))), "sub_text": _scalar(record.get(input_names.get("sub_text"))),
                        "promo_price": _scalar(record.get(input_names.get("promo_price"))), "status": status,
                        "issues": _scalar(record.get(output_names.get("issues"))), "processed_at": _scalar(record.get(output_names.get("processed_at"))),
                        "input_hash": _scalar(record.get(output_names.get("input_hash"))), "image_attachment": image_attachment,
                        "image_file_token": token or None,
                        "source_image_url": f"/api/image?source_id={source_id}&file_token={token}" if token else None,
                        "feishu_record_id": record.get("_record_id"),
                        "product_name": _scalar(products.get(str(product_id or "").strip(), {}).get(product_fields.get("product_name"))),
                        "min_price": _scalar(products.get(str(product_id or "").strip(), {}).get(product_fields.get("min_price"))),
                    })
            send_json(handler, {"source_id": source_id, "records": records, "summary": {"total": len(records)}, "runtime": {"config_persistence": persistence.mode}})
        elif resource == "sources":
            _authorize_admin(handler)
            repo, persistence = config_repository()
            sources = []
            if repo:
                for source_id in repo.list_source_ids():
                    try:
                        config = repo.get_active(source_id)
                        sources.append({"source_id": source_id, "display_name": config.display_name, "active_revision": config.revision, "health": "ACTIVE"})
                    except SemanticLayerError:
                        sources.append({"source_id": source_id, "health": "INACTIVE"})
            send_json(handler, {"sources": sources, "config_persistence": persistence.mode, "capabilities": {"onboarding_write": persistence.mode == "persistent"}})
        else:
            send_json(handler, {"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
    except PermissionError as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
    except Exception as exc:
        _semantic_error(handler, exc)


def handle_demo_run(handler: Any) -> None:
    lock_key = ""
    lock_acquired = False
    try:
        _authorize_public_demo(handler)
        payload = _read_json(handler)

        forbidden_keys = FORBIDDEN_PUBLIC_KEYS & set(payload.keys())
        if forbidden_keys:
            raise ValueError(f"请求包含非法或敏感字段: {', '.join(sorted(forbidden_keys))}")

        action = str(payload.get("action") or "").strip()
        if action == "run_next_pending":
            raise ValueError("公开演示不支持 run_next_pending，请使用 action=run_task 并提供 task_id")
        if action != "run_task":
            raise ValueError("无效的 action 指令")

        whitelist = _get_public_demo_whitelist()
        if not whitelist:
            raise PublicDemoDisabledError("公开演示暂无可用白名单任务")

        if action == "run_task":
            task_id = str(payload.get("task_id") or "").strip()
            if not task_id or len(task_id) > 64:
                raise ValueError("请提供有效的演示任务 ID")
            if task_id not in whitelist:
                raise KeyError(f"任务 {task_id} 不在公开白名单")

            lock_key = f"public:task:{task_id}"
            with _state_lock:
                if lock_key in _running_tasks:
                    raise RequestConflictError("该演示任务正在处理中，请勿重复提交")
                _running_tasks.add(lock_key)
                lock_acquired = True

            report = run_protected_task(task_id)
            send_json(handler, {
                "rules_version": report["rules_version"],
                "pipeline_version": report["pipeline_version"],
                "record": report["records"][0],
                "summary": report["summary"],
                "runtime": report["runtime"],
            })
            return

    except Exception as exc:
        _semantic_error(handler, exc)
    finally:
        if lock_key and lock_acquired:
            with _state_lock:
                _running_tasks.discard(lock_key)


def handle_run(handler: Any) -> None:
    task_id = ""
    lock_key = ""
    lock_acquired = False
    try:
        _authorize_write(handler)
        payload = _read_json(handler)
        task_id = str(payload.get("task_id") or "").strip()
        source_id = str(payload.get("source_id") or "").strip()
        if not task_id or len(task_id) > 64:
            raise ValueError("请选择有效的飞书任务")
        if source_id:
            lock_key = f"{source_id}:{task_id}"
            with _state_lock:
                if lock_key in _running_tasks:
                    raise RequestConflictError("该任务正在处理中，请勿重复提交")
                _running_tasks.add(lock_key)
                lock_acquired = True
            repo, _, _, adapter = _source_context(source_id)
            result = ConfiguredSourceOrchestrator(repo, adapter, output_root=PROJECT_ROOT / "generated_output").run_task(source_id, task_id, dry_run=bool(payload.get("dry_run")), force=bool(payload.get("force")))
            if result.get("image_file_token"):
                result["source_image_url"] = f"/api/image?source_id={source_id}&file_token={result['image_file_token']}"
            send_json(handler, {"record": result, "legacy": False})
            return
        with _state_lock:
            if task_id in _running_tasks:
                raise RequestConflictError("该任务正在处理中，请勿重复提交")
            _running_tasks.add(task_id)
            lock_acquired = True
        report = run_protected_task(task_id)
        send_json(handler, {
            "rules_version": report["rules_version"],
            "pipeline_version": report["pipeline_version"],
            "record": report["records"][0],
            "summary": report["summary"],
            "runtime": report["runtime"],
        })
    except Exception as exc:
        _semantic_error(handler, exc)
    finally:
        if lock_key and lock_acquired:
            with _state_lock:
                _running_tasks.discard(lock_key)
        elif task_id and lock_acquired:
            with _state_lock:
                _running_tasks.discard(task_id)


def handle_sync(handler: Any) -> None:
    lock_name = "__pending_batch__"
    try:
        _authorize_write(handler)
        payload = _read_json(handler) if int(handler.headers.get("Content-Length", "0")) else {}
        source_id = str(payload.get("source_id") or "").strip()
        if source_id:
            source_lock = f"{source_id}:__pending_batch__"
            with _state_lock:
                if source_lock in _running_tasks:
                    raise RequestConflictError("该数据源待审查任务正在同步")
                _running_tasks.add(source_lock)
            try:
                send_json(handler, _source_tasks_for_sync(source_id, payload))
            finally:
                with _state_lock:
                    _running_tasks.discard(source_lock)
            return
        with _state_lock:
            if lock_name in _running_tasks:
                raise RequestConflictError("待审查任务正在同步，请勿重复提交")
            _running_tasks.add(lock_name)
        report = run_protected_pending(limit=3)
        send_json(handler, {
            "rules_version": report["rules_version"],
            "pipeline_version": report["pipeline_version"],
            "records": report["records"],
            "summary": report["summary"],
            "runtime": report["runtime"],
            "batch": report.get("batch", {}),
        })
    except Exception as exc:
        _semantic_error(handler, exc)
    finally:
        with _state_lock:
            _running_tasks.discard(lock_name)


def _source_tasks_for_sync(source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    repo, _, config, adapter = _source_context(source_id)
    table = config.tables["tasks"]
    names = {item["field_id"]: item["field_name"] for item in adapter.list_fields(config.connector.base_token, table.table_id)}
    task_name = names[table.fields["task_id"].field_id]
    status_name = names.get(config.writeback["status"].field_id) if "status" in config.writeback else None
    task_ids = [
        str(_scalar(record.get(task_name)) or "").strip()
        for record in adapter.list_records(config.connector.base_token, table.table_id)
        if str(_scalar(record.get(task_name)) or "").strip()
        and (not status_name or str(_scalar(record.get(status_name)) or "") not in TERMINAL_BASE_STATUSES)
    ]
    orchestrator = ConfiguredSourceOrchestrator(repo, adapter, output_root=PROJECT_ROOT / "generated_output")
    records = [orchestrator.run_task(source_id, task_id, force=bool(payload.get("force"))) for task_id in task_ids[:3]]
    return {"records": records, "summary": {"total": len(records)}, "batch": {"processed": len(records)}, "legacy": False}


def handle_discover(handler: Any) -> None:
    try:
        _authorize_write(handler)
        payload = _read_json(handler)
        source_id, display_name = str(payload.get("source_id") or "").strip(), str(payload.get("display_name") or "").strip()
        credential_ref = str(payload.get("credential_ref") or "FEISHU_PRIMARY_APP")
        snapshot = discover_source(resolve_feishu_credential(credential_ref), source_id=source_id, display_name=display_name, base_token=_source_token(payload.get("base_url") or payload.get("base_token")), credential_ref=credential_ref)
        from .business_semantics import mapping_candidates
        send_json(handler, {"source_id": source_id, "schema_fingerprint": snapshot.schema_fingerprint, "tables": [{"table_id": item.table.table_id, "name": item.table.name, "fields": [{"field_id": field.field_id, "name": field.name, "source_type": field.normalized_type} for field in item.fields]} for item in snapshot.tables], "candidates": [candidate.to_dict() for candidate in mapping_candidates(snapshot)]})
    except Exception as exc:
        _semantic_error(handler, exc)


def handle_confirm(handler: Any) -> None:
    try:
        _authorize_write(handler)
        payload = _read_json(handler)
        repo, persistence = config_repository()
        if repo is None or persistence.mode != "persistent":
            raise RuntimeError("当前环境不支持持久化确认数据源")
        source_id, display_name = str(payload.get("source_id") or "").strip(), str(payload.get("display_name") or "").strip()
        credential_ref = str(payload.get("credential_ref") or "FEISHU_PRIMARY_APP")
        # Reject malformed client selections before opening any connector call.
        selection = _selection_from_payload(payload)
        snapshot = discover_source(resolve_feishu_credential(credential_ref), source_id=source_id, display_name=display_name, base_token=_source_token(payload.get("base_url") or payload.get("base_token")), credential_ref=credential_ref)
        result = confirm_draft_config(repo, snapshot, selection, revision=int(payload.get("revision") or 1), expected_active_revision=payload.get("expected_active_revision"))
        send_json(handler, {"source_id": source_id, "revision": result.config.revision, "status": result.config.status.value, "dry_run": {"passed": result.dry_run.passed, "issues": list(result.dry_run.issues)}})
    except Exception as exc:
        _semantic_error(handler, exc)


def handle_activate(handler: Any) -> None:
    try:
        _authorize_write(handler)
        payload = _read_json(handler)
        repo, persistence = config_repository()
        if repo is None or persistence.mode != "persistent":
            raise RuntimeError("当前环境不支持持久化激活数据源")
        source_id, revision = str(payload.get("source_id") or ""), int(payload.get("revision") or 0)
        repo.activate(source_id, revision, expected_active_revision=payload.get("expected_active_revision"))
        send_json(handler, {"source_id": source_id, "revision": revision, "status": "ACTIVE"})
    except Exception as exc:
        _semantic_error(handler, exc)


def handle_image(handler: Any) -> None:
    from urllib.parse import parse_qs, urlparse

    try:
        query = parse_qs(urlparse(handler.path).query)
        file_token = str((query.get("file_token") or [""])[0])
        source_id = str((query.get("source_id") or [""])[0])
        if not file_token or len(file_token) > 128:
            raise ValueError("图片标识无效")
        if source_id:
            _authorize_admin(handler)
            _, _, config, adapter = _source_context(source_id)
            mapping = config.writeback.get("image_attachment")
            if mapping is None:
                raise PermissionError("当前数据源未映射图片附件字段")
            names = {item["field_id"]: item["field_name"] for item in adapter.list_fields(config.connector.base_token, config.tables["tasks"].table_id)}
            attachment_name = names.get(mapping.field_id)
            allowed = any(file_token == str(item.get("file_token") or "") for record in adapter.list_records(config.connector.base_token, config.tables["tasks"].table_id) for item in (record.get(attachment_name) or []) if isinstance(item, dict))
            if not allowed:
                raise PermissionError("图片不属于当前数据源")
            content, content_type = adapter.download_media(file_token)
            handler.send_response(HTTPStatus.OK)
            handler.send_header("Content-Type", content_type)
            handler.send_header("Content-Length", str(len(content)))
            handler.send_header("Cache-Control", "private, max-age=300")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.end_headers()
            handler.wfile.write(content)
            return
        adapter = FeishuOpenApiAdapter.from_env()
        if adapter is None:
            raise RuntimeError("飞书应用身份尚未配置")
        if not image_token_belongs_to_demo_base(file_token, adapter):
            raise PermissionError("图片不属于当前演示数据源")
        content, content_type = adapter.download_media(file_token)
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(content)))
        handler.send_header("Cache-Control", "private, max-age=300")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.wfile.write(content)
    except ValueError as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
    except PermissionError as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.FORBIDDEN)
    except Exception as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
