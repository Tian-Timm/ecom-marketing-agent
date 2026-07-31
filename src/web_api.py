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
from typing import Any

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

MAX_BODY_BYTES = 8 * 1024
RATE_WINDOW_SECONDS = 10 * 60
RATE_LIMIT = 6
_state_lock = threading.Lock()
_requests_by_ip: dict[str, list[float]] = {}
_running_tasks: set[str] = set()


def send_json(handler: Any, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
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


def _authorize_write(handler: Any) -> None:
    expected = os.environ.get("DEMO_ADMIN_TOKEN", "").strip()
    supplied = handler.headers.get("X-Demo-Admin-Token", "").strip()
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise PermissionError("执行口令无效")

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


def handle_get(handler: Any, resource: str) -> None:
    try:
        if resource == "status":
            send_json(handler, runtime_status())
        elif resource == "tasks":
            send_json(handler, live_report())
        else:
            send_json(handler, {"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
    except Exception as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)


def handle_run(handler: Any) -> None:
    task_id = ""
    try:
        _authorize_write(handler)
        payload = _read_json(handler)
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id or len(task_id) > 64:
            raise ValueError("请选择有效的飞书任务")
        with _state_lock:
            if task_id in _running_tasks:
                raise RuntimeError("该任务正在处理中，请勿重复提交")
            _running_tasks.add(task_id)
        report = run_protected_task(task_id)
        send_json(handler, {
            "rules_version": report["rules_version"],
            "pipeline_version": report["pipeline_version"],
            "record": report["records"][0],
            "summary": report["summary"],
            "runtime": report["runtime"],
        })
    except PermissionError as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
    except KeyError as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.NOT_FOUND)
    except ValueError as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
    except Exception as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
    finally:
        if task_id:
            with _state_lock:
                _running_tasks.discard(task_id)


def handle_sync(handler: Any) -> None:
    lock_name = "__pending_batch__"
    try:
        _authorize_write(handler)
        with _state_lock:
            if lock_name in _running_tasks:
                raise RuntimeError("待审查任务正在同步，请勿重复提交")
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
    except PermissionError as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
    except ValueError as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.BAD_REQUEST)
    except Exception as exc:
        send_json(handler, {"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
    finally:
        with _state_lock:
            _running_tasks.discard(lock_name)


def handle_image(handler: Any) -> None:
    from urllib.parse import parse_qs, urlparse

    try:
        query = parse_qs(urlparse(handler.path).query)
        file_token = str((query.get("file_token") or [""])[0])
        if not file_token or len(file_token) > 128:
            raise ValueError("图片标识无效")
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
