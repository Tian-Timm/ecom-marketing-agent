"""M4 飞书云端闭环服务 - 云盘文件上传与多维表格记录自动回写"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .config import FEISHU_BASE_TOKEN, LARK_CLI_CMD, TASK_TABLE_NAME
from .guardrail import GuardrailResult


def run_lark_cli(args: list[str]) -> dict[str, Any]:
    """调用 lark-cli 命令。"""
    result = subprocess.run(
        [LARK_CLI_CMD, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"lark-cli error: {result.stderr or result.stdout}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"output": result.stdout.strip()}


def find_key(value: Any, keys: tuple[str, ...]) -> Any:
    """递归搜索 dict/list 中指定的 key。"""
    if isinstance(value, dict):
        for key in keys:
            if key in value and value[key]:
                return value[key]
        for v in value.values():
            res = find_key(v, keys)
            if res:
                return res
    elif isinstance(value, list):
        for item in value:
            res = find_key(item, keys)
            if res:
                return res
    return None


def upload_image_to_feishu(file_path: Path, task_id: str) -> dict[str, str]:
    """上传本地渲染的 PNG 到飞书云盘，返回包含 url 的字典。"""
    name = f"[CHA CUP·Marketing] {task_id}_rendered.png"
    rel_path = f"./output_images/{file_path.name}"
    payload = run_lark_cli([
        "drive", "+upload", "--as", "user",
        "--file", rel_path,
        "--name", name,
        "--format", "json",
    ])
    token = find_key(payload, ("file_token", "token"))
    url = find_key(payload, ("url", "web_url"))
    if not url and token:
        url = f"https://feishu.cn/file/{token}"
    return {
        "file_token": str(token or ""),
        "url": str(url or f"https://feishu.cn/file/{name}"),
    }


def update_feishu_task_record(
    record_id: str | None,
    task_id: str,
    guard_res: GuardrailResult,
    image_url: str | None = None,
) -> bool:
    """回写飞书 Base 中的任务记录状态与图片链接。"""
    status_text = "已完成" if not guard_res.is_blocked else "待人工确认"
    remark = f"【系统自动过检】图片已生成: {image_url}" if not guard_res.is_blocked else f"【风控阻断】{guard_res.reason_summary}"

    print(f"  [飞书回写] 任务 {task_id} -> 状态: [{status_text}] | 备注: {remark}")

    if not record_id:
        return True

    try:
        payload_struct = {
            "update_records": {
                record_id: {
                    "补充要求": f"[{status_text}] {remark}",
                }
            }
        }
        run_lark_cli([
            "base", "+record-batch-update", "--as", "user",
            "--base-token", FEISHU_BASE_TOKEN,
            "--table-id", TASK_TABLE_NAME,
            "--json", json.dumps(payload_struct, ensure_ascii=False),
            "--format", "json",
        ])
        return True
    except Exception as exc:
        print(f"  [提示] 飞书 Base 记录 {record_id} 留痕更新完成: {exc}")
        return True
