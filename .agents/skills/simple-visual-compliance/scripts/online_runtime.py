#!/usr/bin/env python3
"""线上工作台的固定 Base 读取与受控单任务执行模块。"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from audit_text import load_rules
from feishu_base_adapter import (
    PROJECT_ROOT,
    build_task_input,
    load_fixture_inputs,
    restore_legacy_input,
    scalar_cell_value,
    sync_base,
)
from feishu_openapi_adapter import FeishuOpenApiAdapter
from run_pipeline import (
    PIPELINE_VERSION,
    SCHEMA_VERSION,
    build_report,
    execute_task,
    fingerprint_task,
)
from semantic_review import DeepSeekSemanticReviewer
from standardize import sanitize_record

CONFIG_PATH = (
    PROJECT_ROOT
    / ".agents"
    / "skills"
    / "simple-visual-compliance"
    / "assets"
    / "feishu_demo_base.json"
)
STATIC_REPORT = PROJECT_ROOT / "generated_output" / "pipeline_result.json"

BASE_TO_STATUS = {
    "待处理": "PENDING",
    "审查通过": "PASSED",
    "需修改": "BLOCKED",
    "待人工复核": "REVIEW_REQUIRED",
    "执行失败": "FAILED",
}


def runtime_status() -> Dict[str, Any]:
    config = load_config()
    feishu = FeishuOpenApiAdapter.from_env() is not None
    deepseek = DeepSeekSemanticReviewer.from_env() is not None
    write_protected = bool(os.environ.get("DEMO_ADMIN_TOKEN", "").strip())
    return {
        "online": feishu,
        "mode": "live" if feishu else "snapshot",
        "rules_version": str(load_rules().get("version", "unknown")),
        "pipeline_version": PIPELINE_VERSION,
        "source": {
            "name": config["name"],
            "url": config["url"],
            "type": "feishu_base",
        },
        "capabilities": {
            "feishu_read": feishu,
            "feishu_write": feishu and write_protected,
            "semantic_review": deepseek,
            "image_delivery": feishu and write_protected,
        },
    }


def load_config() -> Dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _attachment_token(value: Any) -> str | None:
    if not isinstance(value, list):
        return None
    for item in value:
        if isinstance(item, dict) and item.get("file_token"):
            return str(item["file_token"])
    return None


def _record_from_base(
    task: Dict[str, Any],
    product: Dict[str, Any],
    config: Dict[str, Any],
    index: int,
) -> Dict[str, Any]:
    task_input = build_task_input(task, product)
    normalized = sanitize_record(task_input, index)
    output_fields = config["output_fields"]
    base_status = scalar_cell_value(task.get(output_fields["status"]))
    status = BASE_TO_STATUS.get(base_status, "PENDING")
    issues = str(task.get(output_fields["issues"]) or "").strip()
    violations = [
        {
            "code": "BASE_RECORDED_ISSUE",
            "field": "copy",
            "message": message,
            "value": "",
            "source": "pipeline",
        }
        for message in issues.split("；")
        if message
    ]
    attachment_token = _attachment_token(
        task.get(output_fields.get("image_attachment", ""))
    )
    task_id = str(normalized.get("task_id") or "")
    generated_filename = f"{task_id}_rendered.png" if status == "PASSED" else None
    image_url = (
        f"/api/image?file_token={attachment_token}"
        if attachment_token
        else (
            f"generated_output/{generated_filename}"
            if generated_filename
            else None
        )
    )
    return {
        **normalized,
        "status": status,
        "blocked_reason": issues,
        "violations": violations,
        "rules_version": str(load_rules().get("version", "unknown")),
        "generated_image": generated_filename,
        "generated_image_url": image_url,
        "artifact": None,
        "input_hash": (
            scalar_cell_value(task.get(output_fields["input_hash"]))
            or fingerprint_task(task_input, index)
        ),
        "pipeline_version": scalar_cell_value(
            task.get(output_fields["pipeline_version"])
        ) or PIPELINE_VERSION,
        "source": config["url"],
        "started_at": str(task.get(output_fields["processed_at"]) or ""),
        "duration_ms": 0,
        "execution_trace": [
            {
                "name": "normalize",
                "label": "信息整理",
                "status": "COMPLETED",
                "duration_ms": 0,
                "detail": "已从飞书读取任务信息。",
            },
            {
                "name": "deterministic_audit",
                "label": "规则审查",
                "status": (
                    "COMPLETED"
                    if status == "PASSED"
                    else ("SKIPPED" if status == "PENDING" else "BLOCKED")
                ),
                "duration_ms": 0,
                "detail": issues or (
                    "历史规则审查通过。"
                    if status == "PASSED"
                    else "等待执行审查。"
                ),
            },
            {
                "name": "semantic_review",
                "label": "语义复核",
                "status": "SKIPPED",
                "duration_ms": 0,
                "detail": "当前展示飞书中已保存的处理结果。",
            },
            {
                "name": "render",
                "label": "图片生成",
                "status": "COMPLETED" if generated_filename else "SKIPPED",
                "duration_ms": 0,
                "detail": "营销图片已生成。" if generated_filename else "未生成图片。",
            },
            {
                "name": "delivery",
                "label": "飞书交付",
                "status": "COMPLETED" if base_status else "SKIPPED",
                "duration_ms": 0,
                "detail": "处理结果已保存在飞书多维表格。",
            },
        ],
        "feishu_record_id": task.get("_record_id"),
        "feishu_base_url": config["url"],
        "feishu_image_url": task.get(output_fields["image_url"]),
        "feishu_file_token": attachment_token,
        "sync_status": "LOADED_FROM_BASE",
    }


def _summary(records: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "total": len(records),
        "pending": sum(item["status"] == "PENDING" for item in records),
        "passed": sum(item["status"] == "PASSED" for item in records),
        "blocked": sum(item["status"] == "BLOCKED" for item in records),
        "review_required": sum(
            item["status"] == "REVIEW_REQUIRED" for item in records
        ),
        "failed": sum(item["status"] == "FAILED" for item in records),
        "images_generated": sum(bool(item.get("generated_image")) for item in records),
    }


def live_report(adapter: FeishuOpenApiAdapter | None = None) -> Dict[str, Any]:
    adapter = adapter or FeishuOpenApiAdapter.from_env()
    if adapter is None:
        if not STATIC_REPORT.exists():
            raise RuntimeError("飞书应用尚未配置，且没有可用的演示快照")
        report = json.loads(STATIC_REPORT.read_text(encoding="utf-8"))
        report["runtime"] = runtime_status()
        return report

    config = load_config()
    products = adapter.list_records(
        config["base_token"],
        config["product_table"]["id"],
    )
    if len(products) != 1:
        raise RuntimeError("演示 Base 的商品资料必须保持为一条")
    fixtures = load_fixture_inputs(PROJECT_ROOT / config["legacy_input_fixture"])
    tasks = [
        restore_legacy_input(task, fixtures)
        for task in adapter.list_records(
            config["base_token"],
            config["task_table"]["id"],
        )
    ]
    records = [
        _record_from_base(task, products[0], config, index)
        for index, task in enumerate(tasks)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules_version": str(load_rules().get("version", "unknown")),
        "source": config["url"],
        "summary": _summary(records),
        "records": records,
        "runtime": runtime_status(),
        "feishu_sync": {
            "base_name": config["name"],
            "base_url": config["url"],
            "records_read": len(records),
            "mode": "live",
        },
    }


def image_token_belongs_to_demo_base(
    file_token: str,
    adapter: FeishuOpenApiAdapter,
) -> bool:
    """仅允许代理固定演示 Base 已回写的图片附件。"""
    config = load_config()
    attachment_field = config["output_fields"].get("image_attachment")
    if not attachment_field:
        return False
    tasks = adapter.list_records(
        config["base_token"],
        config["task_table"]["id"],
    )
    return any(
        _attachment_token(task.get(attachment_field)) == file_token
        for task in tasks
    )


def _public_demo_task_ids() -> set[str]:
    raw = os.environ.get("PUBLIC_DEMO_TASK_IDS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def run_public_demo_task(task_id: str) -> Dict[str, Any]:
    """实时读取并只读执行一个公开白名单任务。"""
    task_id = str(task_id or "").strip()
    if not task_id or task_id not in _public_demo_task_ids():
        raise KeyError(f"任务 {task_id} 不在公开白名单")

    adapter = FeishuOpenApiAdapter.from_env()
    if adapter is None:
        raise RuntimeError("飞书应用身份尚未配置")
    semantic_reviewer = DeepSeekSemanticReviewer.from_env()
    if semantic_reviewer is None:
        raise RuntimeError("DeepSeek 语义复核尚未配置")

    config = load_config()
    products = adapter.list_records(
        config["base_token"],
        config["product_table"]["id"],
    )
    if len(products) != 1:
        raise RuntimeError("演示 Base 的商品资料必须保持为一条")

    fixture_path = PROJECT_ROOT / str(config["legacy_input_fixture"])
    fixtures = load_fixture_inputs(fixture_path)
    task_records = adapter.list_records(
        config["base_token"],
        config["task_table"]["id"],
    )
    selected = next(
        (
            (index, restore_legacy_input(task, fixtures))
            for index, task in enumerate(task_records)
            if scalar_cell_value(task.get("任务ID")) == task_id
        ),
        None,
    )
    if selected is None:
        raise KeyError(f"没有找到任务 {task_id}")

    index, task = selected
    task_input = build_task_input(task, products[0])
    rules = load_rules()
    with tempfile.TemporaryDirectory(prefix="cha-cup-public-demo-") as temp_dir:
        record = execute_task(
            task_input,
            Path(temp_dir),
            index=index,
            rules=rules,
            source=config["url"],
            semantic_reviewer=semantic_reviewer,
        )

    report = build_report([record], source=config["url"], rules=rules)
    report["runtime"] = runtime_status()
    report["execution_mode"] = "live_readonly"
    report["writeback"] = False
    report["feishu_sync"] = {
        "base_name": config["name"],
        "base_url": config["url"],
        "records_read": len(products) + len(task_records),
        "mode": "live_readonly",
        "writeback": False,
    }
    return report


def run_protected_task(task_id: str) -> Dict[str, Any]:
    adapter = FeishuOpenApiAdapter.from_env()
    if adapter is None:
        raise RuntimeError("飞书应用身份尚未配置")
    if DeepSeekSemanticReviewer.from_env() is None:
        raise RuntimeError("DeepSeek 语义复核尚未配置")
    output_dir = (
        Path(tempfile.gettempdir()) / "cha-cup-output"
        if os.environ.get("VERCEL")
        else PROJECT_ROOT / "generated_output"
    )
    report = sync_base(
        CONFIG_PATH,
        output_dir,
        cli=adapter,
        task_ids={task_id},
        max_records=1,
    )
    if not report["records"]:
        raise KeyError(f"没有找到任务 {task_id}")
    report["runtime"] = runtime_status()
    return report


def run_protected_pending(limit: int = 3) -> Dict[str, Any]:
    """只处理固定 Base 中待审查的少量任务，避免线上批量误触发。"""
    adapter = FeishuOpenApiAdapter.from_env()
    if adapter is None:
        raise RuntimeError("飞书应用身份尚未配置")
    if DeepSeekSemanticReviewer.from_env() is None:
        raise RuntimeError("DeepSeek 语义复核尚未配置")

    config = load_config()
    task_records = adapter.list_records(
        config["base_token"],
        config["task_table"]["id"],
    )
    status_field = config["output_fields"]["status"]
    pending_ids = {
        str(task.get("任务ID") or "").strip()
        for task in task_records
        if scalar_cell_value(task.get(status_field)) in {"", "待处理"}
        and str(task.get("任务ID") or "").strip()
    }
    if not pending_ids:
        report = live_report(adapter)
        report["batch"] = {"requested": 0, "processed": 0, "limit": limit}
        return report

    output_dir = (
        Path(tempfile.gettempdir()) / "cha-cup-output"
        if os.environ.get("VERCEL")
        else PROJECT_ROOT / "generated_output"
    )
    selected_ids = set(sorted(pending_ids)[: max(1, min(limit, 3))])
    report = sync_base(
        CONFIG_PATH,
        output_dir,
        cli=adapter,
        task_ids=selected_ids,
        max_records=3,
    )
    report["runtime"] = runtime_status()
    report["batch"] = {
        "requested": len(selected_ids),
        "processed": len(report["records"]),
        "limit": 3,
    }
    return report
