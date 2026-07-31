#!/usr/bin/env python3
"""固定演示 Base 的读取、执行、云盘上传与结果回写适配器。"""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from audit_text import load_rules
from run_pipeline import (
    build_report,
    execute_task,
    fingerprint_task,
    write_report,
)

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = SKILL_DIR.parents[2]

LEGACY_OUTPUT_PREFIXES = ("[已完成]", "[待人工确认]")
TERMINAL_BASE_STATUSES = {"审查通过", "需修改", "待人工复核", "执行失败"}
STATUS_TO_BASE = {
    "PASSED": "审查通过",
    "BLOCKED": "需修改",
    "REVIEW_REQUIRED": "待人工复核",
    "FAILED": "执行失败",
}


def scalar_cell_value(value: Any) -> str:
    """Normalize Base cells such as single-select values returned as one-item lists."""
    if isinstance(value, list):
        return str(value[0]) if len(value) == 1 else ""
    return str(value or "")


def _find_value(payload: Any, keys: Iterable[str]) -> Any:
    if isinstance(payload, dict):
        for key in keys:
            if payload.get(key):
                return payload[key]
        for value in payload.values():
            found = _find_value(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_value(value, keys)
            if found:
                return found
    return None


def records_from_envelope(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if payload.get("ok") is not True:
        raise RuntimeError(f"飞书读取失败: {payload.get('error') or payload}")
    data = payload.get("data", {})
    fields = list(data.get("fields", []))
    rows = list(data.get("data", []))
    record_ids = list(data.get("record_id_list", []))
    if data.get("has_more"):
        raise RuntimeError("固定演示 Base 超过单页范围，当前同步拒绝基于不完整数据继续")
    records: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        record = dict(zip(fields, row))
        record["_record_id"] = record_ids[index] if index < len(record_ids) else None
        records.append(record)
    return records


def load_fixture_inputs(path: Path) -> Dict[str, Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {
            str(row.get("任务ID") or "").strip(): dict(row)
            for row in csv.DictReader(file)
            if row.get("任务ID")
        }


def restore_legacy_input(
    task: Dict[str, Any],
    fixtures: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    restored = dict(task)
    current = str(restored.get("补充要求") or "").strip()
    task_id = str(restored.get("任务ID") or "").strip()
    if current.startswith(LEGACY_OUTPUT_PREFIXES) and task_id in fixtures:
        restored["补充要求"] = fixtures[task_id].get("补充要求") or ""
        restored["_legacy_input_restored"] = True
    else:
        restored["_legacy_input_restored"] = False
    return restored


def build_task_input(
    task: Dict[str, Any],
    product: Dict[str, Any],
) -> Dict[str, Any]:
    output_names = {
        "审查状态",
        "问题说明",
        "生成图片链接",
        "生成图片",
        "处理时间",
        "输入指纹",
        "处理版本",
    }
    task_input = {
        key: value
        for key, value in task.items()
        if not key.startswith("_") and key not in output_names
    }
    task_input["最低允许促销价"] = product.get("最低允许促销价")
    task_input["商品名称"] = product.get("商品名称")
    return task_input


def update_fields_for_result(
    result: Dict[str, Any],
    *,
    image_url: str | None,
    image_attachment: List[Dict[str, Any]] | None = None,
    sub_text: str,
    processed_at: str,
    output_fields: Dict[str, str],
) -> Dict[str, Any]:
    issues = "；".join(
        str(item.get("message") or "")
        for item in result.get("violations", [])
        if item.get("message")
    )
    if result.get("status") == "FAILED":
        issues = str((result.get("error") or {}).get("message") or issues or "执行失败")
    image_value = (
        f"[{result.get('task_id')} 营销图片]({image_url})"
        if image_url
        else None
    )
    try:
        dt = datetime.strptime(processed_at, "%Y-%m-%d %H:%M:%S")
        processed_at_val: Any = int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        processed_at_val = processed_at

    fields = {
        "补充要求": sub_text,
        output_fields["status"]: STATUS_TO_BASE.get(result.get("status"), "执行失败"),
        output_fields["issues"]: issues or None,
        output_fields["image_url"]: image_value,
        output_fields["processed_at"]: processed_at_val,
        output_fields["input_hash"]: result.get("input_hash"),
        output_fields["pipeline_version"]: (
            f"规则 {result.get('rules_version')} / 流水线 {result.get('pipeline_version')}"
        ),
    }
    attachment_field = output_fields.get("image_attachment")
    if attachment_field:
        fields[attachment_field] = image_attachment
    return fields


class LarkCliAdapter:
    """在 lark-cli 进程 seam 上提供 Base 与 Drive 两种真实适配器行为。"""

    def __init__(self, project_root: Path = PROJECT_ROOT) -> None:
        self.project_root = project_root
        self.executable = (
            shutil.which("lark-cli.cmd")
            or shutil.which("lark-cli")
            or "lark-cli"
        )

    def run(self, args: List[str]) -> Dict[str, Any]:
        env = dict(os.environ)
        env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
        completed = subprocess.run(
            [self.executable, *args],
            cwd=self.project_root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        output = completed.stdout or completed.stderr
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"lark-cli 返回了无法解析的内容: {output}") from exc
        if completed.returncode != 0 or payload.get("ok") is not True:
            raise RuntimeError(
                str((payload.get("error") or {}).get("message") or output)
            )
        return payload

    def list_records(self, base_token: str, table_id: str) -> List[Dict[str, Any]]:
        payload = self.run([
            "base",
            "+record-list",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--limit",
            "200",
            "--format",
            "json",
            "--as",
            "user",
        ])
        return records_from_envelope(payload)

    def upload_image(self, file_path: Path, task_id: str) -> str:
        relative = file_path.resolve().relative_to(self.project_root.resolve())
        payload = self.run([
            "drive",
            "+upload",
            "--file",
            relative.as_posix(),
            "--name",
            f"CHA CUP {task_id} 营销图片.png",
            "--format",
            "json",
            "--as",
            "user",
        ])
        url = _find_value(payload, ("url", "web_url"))
        token = _find_value(payload, ("file_token", "token"))
        if url:
            return str(url)
        if token:
            return f"https://acn4y3muxbcy.feishu.cn/file/{token}"
        raise RuntimeError("图片已上传，但飞书没有返回可回写的文件链接")

    def batch_update(
        self,
        base_token: str,
        table_id: str,
        updates: Dict[str, Dict[str, Any]],
    ) -> None:
        self.run([
            "base",
            "+record-batch-update",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--json",
            json.dumps({"update_records": updates}, ensure_ascii=False),
            "--format",
            "json",
            "--as",
            "user",
        ])


def _set_delivery_trace(
    result: Dict[str, Any],
    status: str,
    detail: str,
) -> None:
    for step in result.get("execution_trace", []):
        if step.get("name") == "delivery":
            step["status"] = status
            step["detail"] = detail
            return


def sync_base(
    config_path: Path,
    output_dir: Path,
    *,
    cli: LarkCliAdapter | None = None,
    dry_run: bool = False,
    force: bool = False,
    task_ids: set[str] | None = None,
    max_records: int | None = None,
) -> Dict[str, Any]:
    """读取固定 Base，执行全部任务，并按输入指纹幂等回写。"""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    cli = cli or LarkCliAdapter()
    base_token = str(config["base_token"])
    product_table_id = str(config["product_table"]["id"])
    task_table_id = str(config["task_table"]["id"])
    output_fields = dict(config["output_fields"])

    products = cli.list_records(base_token, product_table_id)
    all_tasks = cli.list_records(base_token, task_table_id)
    tasks = [
        task
        for task in all_tasks
        if not task_ids or str(task.get("任务ID") or "") in task_ids
    ]
    if max_records is not None:
        tasks = tasks[:max(0, max_records)]
    if len(products) != 1:
        raise RuntimeError(f"固定演示 Base 应有且仅有 1 条商品资料，当前为 {len(products)} 条")

    fixture_path = PROJECT_ROOT / str(config["legacy_input_fixture"])
    fixtures = load_fixture_inputs(fixture_path)
    product = products[0]
    rules = load_rules()
    results: List[Dict[str, Any]] = []
    updates: Dict[str, Dict[str, Any]] = {}
    uploaded = 0
    skipped = 0
    restored = 0
    processed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for index, original_task in enumerate(tasks):
        task = restore_legacy_input(original_task, fixtures)
        restored += int(bool(task["_legacy_input_restored"]))
        task_input = build_task_input(task, product)
        expected_hash = fingerprint_task(task_input, index)
        existing_hash = scalar_cell_value(task.get(output_fields["input_hash"]))
        existing_status = scalar_cell_value(task.get(output_fields["status"]))
        unchanged = (
            not force
            and expected_hash == existing_hash
            and existing_status in TERMINAL_BASE_STATUSES
        )

        if unchanged:
            result = execute_task(
                task_input,
                output_dir,
                index=index,
                rules=rules,
                source=config["url"],
                semantic_reviewer=None,
            )
            result["feishu_record_id"] = task.get("_record_id")
            result["feishu_base_url"] = config["url"]
            result["feishu_image_url"] = task.get(output_fields["image_url"])
            attachment_field = output_fields.get("image_attachment")
            if attachment_field:
                result["feishu_image_attachment"] = task.get(attachment_field)
            skipped += 1
            result["sync_status"] = "SKIPPED_UNCHANGED"
            _set_delivery_trace(
                result,
                "SKIPPED",
                "输入未变化，沿用飞书中的处理结果。",
            )
            results.append(result)
            continue

        result = execute_task(
            task_input,
            output_dir,
            index=index,
            rules=rules,
            source=config["url"],
        )
        result["feishu_record_id"] = task.get("_record_id")
        result["feishu_base_url"] = config["url"]
        result["feishu_image_url"] = task.get(output_fields["image_url"])

        image_url: str | None = None
        image_attachment: List[Dict[str, Any]] | None = None
        if result["status"] == "PASSED" and not dry_run:
            image_path = output_dir / str(result["generated_image"])
            try:
                uploaded_image = cli.upload_image(
                    image_path,
                    str(result["task_id"]),
                )
                if isinstance(uploaded_image, dict):
                    image_url = uploaded_image.get("url")
                    attachment = uploaded_image.get("attachment")
                    if isinstance(attachment, list):
                        image_attachment = attachment
                    result["feishu_file_token"] = uploaded_image.get("file_token")
                else:
                    image_url = str(uploaded_image)
                uploaded += 1
            except Exception as exc:
                result["status"] = "FAILED"
                result["generated_image"] = None
                result["artifact"] = None
                result["error"] = {
                    "code": "FEISHU_UPLOAD_FAILED",
                    "message": str(exc),
                }

        if dry_run:
            result["sync_status"] = "DRY_RUN"
            _set_delivery_trace(result, "SKIPPED", "试运行模式，未上传或回写飞书。")
        else:
            record_id = str(task.get("_record_id") or "")
            if not record_id:
                raise RuntimeError(f"任务 {result.get('task_id')} 缺少飞书 record_id")
            updates[record_id] = update_fields_for_result(
                result,
                image_url=image_url,
                image_attachment=image_attachment,
                sub_text=str(task.get("补充要求") or ""),
                processed_at=processed_at,
                output_fields=output_fields,
            )
            result["sync_status"] = "PENDING_WRITEBACK"
            result["feishu_image_url"] = image_url
        results.append(result)

    if updates and not dry_run:
        cli.batch_update(base_token, task_table_id, updates)
        for result in results:
            if result.get("sync_status") == "PENDING_WRITEBACK":
                result["sync_status"] = "COMPLETED"
                detail = (
                    "图片已上传飞书云盘，审查结果已回写多维表格。"
                    if result.get("feishu_image_url")
                    else "审查结果已回写多维表格。"
                )
                _set_delivery_trace(result, "COMPLETED", detail)

    report = build_report(results, source=config["url"], rules=rules)
    report["feishu_sync"] = {
        "base_name": config["name"],
        "base_url": config["url"],
        "dry_run": dry_run,
        "force": force,
        "records_read": len(tasks),
        "records_available": len(all_tasks),
        "records_written": 0 if dry_run else len(updates),
        "images_uploaded": uploaded,
        "unchanged_skipped": skipped,
        "legacy_inputs_restored": restored,
    }
    report_name = "feishu_dry_run_result.json" if dry_run else "pipeline_result.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / report_name).write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not dry_run:
        write_report(report, output_dir)
    return report
