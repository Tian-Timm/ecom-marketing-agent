#!/usr/bin/env python3
"""Dataset Standardizer Script.

Normalizes raw CSV or JSON data into standard layout & compliance pipeline schemas.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

COLUMN_ALIASES: Dict[str, List[str]] = {
    "task_id": ["任务ID", "ID", "task_id", "编号"],
    "product_name": ["商品名称", "商品", "product_name", "品名"],
    "main_text": ["主文案", "文案", "宣传语", "广告语", "标题", "main_text"],
    "sub_text": ["补充要求", "副文案", "备注", "sub_text"],
    "promo_price": ["活动价", "折后价", "促销价", "现价", "价格", "promo_price"],
    "min_price": ["最低允许促销价", "最低价", "底价", "min_price"],
    "aspect_ratio": ["画布比例", "比例", "尺寸", "aspect_ratio"],
    "img_type": ["图片类型", "类型", "img_type"],
    "deploy_date": ["投放日期", "投放时间", "deploy_date"],
    "campaign_name": ["活动名称", "活动", "campaign_name"],
    "campaign_start": ["活动开始日期", "活动开始时间", "开始日期", "campaign_start"],
    "campaign_end": ["活动结束日期", "活动结束时间", "结束日期", "campaign_end"],
    "template_id": ["模板", "模板ID", "设计模板", "template_id"],
}

def clean_number(val: Any) -> float | None:
    if val is None or val == "":
        return None
    s = str(val).replace("￥", "").replace("元", "").strip()
    match = re.search(r"[-+]?\d*\.\d+|\d+", s)
    if match:
        try:
            return float(match.group(0))
        except ValueError:
            return None
    return None

def match_column(col_name: str) -> str | None:
    col_clean = col_name.strip()
    for std_key, aliases in COLUMN_ALIASES.items():
        if col_clean in aliases:
            return std_key
    return None

def sanitize_record(raw_rec: Dict[str, Any], index: int) -> Dict[str, Any]:
    record: Dict[str, Any] = {}
    for k, v in raw_rec.items():
        std_key = match_column(k)
        if std_key:
            record[std_key] = v

    task_id = record.get("task_id") or f"TASK-{index+1:03d}"
    record["task_id"] = str(task_id).strip()

    main_text = record.get("main_text") or ""
    record["main_text"] = str(main_text).strip()

    sub_text = record.get("sub_text") or ""
    record["sub_text"] = str(sub_text).strip()

    record["promo_price"] = clean_number(record.get("promo_price"))
    record["min_price"] = clean_number(record.get("min_price")) or 89.0

    aspect_ratio = record.get("aspect_ratio") or "1:1"
    record["aspect_ratio"] = str(aspect_ratio).strip()

    img_type = record.get("img_type") or "电商主图"
    record["img_type"] = str(img_type).strip()

    deploy_date = record.get("deploy_date") or ""
    record["deploy_date"] = str(deploy_date).strip()

    campaign_name = record.get("campaign_name") or ""
    record["campaign_name"] = str(campaign_name).strip()

    campaign_start = record.get("campaign_start") or ""
    record["campaign_start"] = str(campaign_start).strip()

    campaign_end = record.get("campaign_end") or ""
    record["campaign_end"] = str(campaign_end).strip()

    # Missing selections intentionally remain blank here.  The template loader
    # resolves that to the built-in default, keeping this normalizer independent
    # from rendering assets while preserving an explicitly supplied value.
    record["template_id"] = str(record.get("template_id") or "").strip()

    return record

def process_file(input_path: Path) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if input_path.suffix.lower() == ".csv":
        with input_path.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                results.append(sanitize_record(row, idx))
    elif input_path.suffix.lower() == ".json":
        with input_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                for idx, row in enumerate(data):
                    results.append(sanitize_record(row, idx))
            elif isinstance(data, dict):
                results.append(sanitize_record(data, 0))
            else:
                raise ValueError("JSON 输入必须是对象或对象数组")
    else:
        raise ValueError("仅支持 CSV 或 JSON 输入")
    return results

def main() -> None:
    parser = argparse.ArgumentParser(description="Standardize marketing task dataset.")
    parser.add_argument("--input", "-i", required=True, help="Input CSV or JSON path")
    parser.add_argument("--output", "-o", help="Output JSON path")
    args = parser.parse_args()

    input_file = Path(args.input)
    if not input_file.exists():
        print(f"Error: input file {input_file} does not exist", file=sys.stderr)
        sys.exit(1)

    standardized = process_file(input_file)
    output_json = json.dumps(standardized, ensure_ascii=False, indent=2)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_json, encoding="utf-8")
        print(f"Standardized {len(standardized)} records -> {out_path}")
    else:
        print(output_json)

if __name__ == "__main__":
    main()
