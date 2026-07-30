"""M1 数据读取服务 - 从飞书 Base / 本地文件拉取业务数据"""

from __future__ import annotations

import csv
import json
import subprocess
from typing import Any

from .config import (
    ASSETS_DIR,
    FEISHU_BASE_TOKEN,
    LARK_CLI_CMD,
    PRODUCT_TABLE_NAME,
    TASK_TABLE_NAME,
)


def run_lark_cli(args: list[str]) -> dict[str, Any]:
    """调用 lark-cli 并返回 JSON 结果。"""
    result = subprocess.run(
        [LARK_CLI_CMD, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"lark-cli 调用失败: {result.stderr or result.stdout}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"lark-cli 返回非 JSON 格式: {result.stdout}") from exc


def fetch_product_data() -> dict[str, Any]:
    """从飞书 Base 读取单品资料，失败时降级从本地 product.csv 读取。"""
    try:
        payload = run_lark_cli([
            "base", "+record-list", "--as", "user",
            "--base-token", FEISHU_BASE_TOKEN,
            "--table-id", PRODUCT_TABLE_NAME,
            "--limit", "10", "--format", "json",
        ])
        data = payload.get("data", {})
        fields = data.get("fields", [])
        rows = data.get("data", [])
        if rows:
            return dict(zip(fields, rows[0]))
    except Exception as exc:
        print(f"[警告] 飞书 Base 读取单品失败，切换本地降级: {exc}")

    # 本地 CSV 降级
    csv_path = ASSETS_DIR / "product.csv"
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            return {
                "商品ID": row.get("商品ID"),
                "商品名称": row.get("商品名称"),
                "SKU": row.get("SKU"),
                "容量": row.get("容量"),
                "颜色": row.get("颜色"),
                "日常价": float(row["日常价"]) if row.get("日常价") else None,
                "最低允许促销价": float(row["最低允许促销价"]) if row.get("最低允许促销价") else 89.0,
                "核心卖点": row.get("核心卖点"),
                "产品图文件名": row.get("产品图文件名"),
                "Logo文件名": row.get("Logo文件名"),
            }
    raise RuntimeError("无法获取商品数据")


def fetch_marketing_tasks() -> list[dict[str, Any]]:
    """从飞书 Base 读取全部出图任务，失败时降级从本地 marketing_tasks.csv 读取。"""
    try:
        payload = run_lark_cli([
            "base", "+record-list", "--as", "user",
            "--base-token", FEISHU_BASE_TOKEN,
            "--table-id", TASK_TABLE_NAME,
            "--limit", "50", "--format", "json",
        ])
        data = payload.get("data", {})
        fields = data.get("fields", [])
        rows = data.get("data", [])
        if rows:
            record_ids = data.get("record_id_list", [])
            tasks = []
            for idx, row in enumerate(rows):
                task_dict = dict(zip(fields, row))
                if idx < len(record_ids):
                    task_dict["record_id"] = record_ids[idx]
                tasks.append(task_dict)
            return tasks
    except Exception as exc:
        print(f"[警告] 飞书 Base 读取出图任务失败，切换本地降级: {exc}")

    # 本地 CSV 降级
    csv_path = ASSETS_DIR / "marketing_tasks.csv"
    tasks = []
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            price_val = float(row["活动价"]) if row.get("活动价") and row["活动价"].strip() else None
            tasks.append({
                "任务ID": row.get("任务ID"),
                "图片类型": row.get("图片类型"),
                "画布比例": row.get("画布比例"),
                "投放日期": row.get("投放日期"),
                "活动名称": row.get("活动名称"),
                "活动价": price_val,
                "活动开始日期": row.get("活动开始日期"),
                "活动结束日期": row.get("活动结束日期"),
                "主文案": row.get("主文案"),
                "补充要求": row.get("补充要求"),
            })
    return tasks
