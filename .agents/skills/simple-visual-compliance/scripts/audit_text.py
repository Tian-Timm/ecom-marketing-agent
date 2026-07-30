#!/usr/bin/env python3
"""营销任务的确定性业务规则审计。"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List

SKILL_DIR = Path(__file__).resolve().parents[1]
RULES_FILE = SKILL_DIR / "rules" / "forbidden_words.json"

def load_rules() -> Dict[str, Any]:
    if RULES_FILE.exists():
        return json.loads(RULES_FILE.read_text(encoding="utf-8"))
    return {
        "version": "fallback",
        "forbidden_words": ["全网第一", "绝对保温", "永不漏水", "100%不漏"],
        "forbidden_appearance_patterns": [
            "改(?:成|为).{0,8}(?:红|粉|蓝|绿|黑|白|黄|紫|橙|灰|色)",
            "(?:改|换|变)(?:杯身|杯盖|杯型|颜色|色)",
        ],
        "allowed_ratios": ["1:1", "3:4"],
        "min_allowed_price": 89.0,
        "campaign_start": "2026-07-01",
        "campaign_end": "2026-07-31"
    }

def _violation(code: str, field: str, message: str, value: Any = None) -> Dict[str, Any]:
    return {
        "code": code,
        "field": field,
        "message": message,
        "value": value,
    }

def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None

def audit_record(rec: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(rec)
    main_text = str(rec.get("main_text") or "").strip()
    sub_text = str(rec.get("sub_text") or "").strip()
    promo_price = rec.get("promo_price")
    min_price_raw = rec.get("min_price")
    min_price = rules.get("min_allowed_price", 89.0) if min_price_raw in (None, "") else min_price_raw
    aspect_ratio = str(rec.get("aspect_ratio") or "1:1").strip()
    deploy_date_text = str(rec.get("deploy_date") or rec.get("投放日期") or "").strip()
    campaign_start_text = str(
        rec.get("campaign_start") or rules.get("campaign_start") or ""
    ).strip()
    campaign_end_text = str(
        rec.get("campaign_end") or rules.get("campaign_end") or ""
    ).strip()

    violations: List[Dict[str, Any]] = []

    # 1. 必填与活动价逻辑
    if promo_price is None or promo_price == "":
        violations.append(_violation("PRICE_MISSING", "promo_price", "活动价缺失"))
    else:
        try:
            promo_price_num = float(promo_price)
            min_price_num = float(min_price)
            if promo_price_num < min_price_num:
                violations.append(_violation(
                    "PRICE_BELOW_MINIMUM",
                    "promo_price",
                    f"活动价 {promo_price_num:g} 元低于最低允许价格 {min_price_num:g} 元",
                    promo_price,
                ))
        except (TypeError, ValueError):
            violations.append(_violation(
                "PRICE_FORMAT_INVALID",
                "promo_price",
                "活动价必须是有效数字",
                promo_price,
            ))

    # 2. 画布比例规格逻辑
    allowed_ratios = rules.get("allowed_ratios", ["1:1", "3:4"])
    if aspect_ratio not in allowed_ratios:
        violations.append(_violation(
            "CANVAS_RATIO_NOT_ALLOWED",
            "aspect_ratio",
            f"画布比例 {aspect_ratio or '空'} 不在允许范围",
            aspect_ratio,
        ))

    # 3. 投放日期有效性逻辑
    deploy_date = _parse_date(deploy_date_text)
    campaign_start = _parse_date(campaign_start_text)
    campaign_end = _parse_date(campaign_end_text)
    if not deploy_date_text:
        violations.append(_violation("DEPLOY_DATE_MISSING", "deploy_date", "投放日期缺失"))
    elif deploy_date is None:
        violations.append(_violation(
            "DEPLOY_DATE_FORMAT_INVALID",
            "deploy_date",
            "投放日期必须使用 YYYY-MM-DD 格式",
            deploy_date_text,
        ))
    if campaign_start is None or campaign_end is None:
        violations.append(_violation(
            "CAMPAIGN_DATE_FORMAT_INVALID",
            "campaign_period",
            "活动开始和结束日期必须使用 YYYY-MM-DD 格式",
            f"{campaign_start_text} 至 {campaign_end_text}",
        ))
    elif campaign_start > campaign_end:
        violations.append(_violation(
            "CAMPAIGN_DATE_RANGE_INVALID",
            "campaign_period",
            "活动开始日期不能晚于结束日期",
            f"{campaign_start_text} 至 {campaign_end_text}",
        ))
    elif deploy_date and not (campaign_start <= deploy_date <= campaign_end):
        violations.append(_violation(
            "DEPLOY_DATE_OUTSIDE_CAMPAIGN",
            "deploy_date",
            f"投放日期 {deploy_date_text} 超出活动周期 {campaign_start_text} 至 {campaign_end_text}",
            deploy_date_text,
        ))

    # 4. 必填文案逻辑
    if not main_text:
        violations.append(_violation("MAIN_TEXT_MISSING", "main_text", "主文案缺失"))

    # 5. 广告法违禁词逻辑
    full_text = f"{main_text} {sub_text}"
    for word in rules.get("forbidden_words", []):
        if word in full_text:
            violations.append(_violation(
                "FORBIDDEN_WORD_DETECTED",
                "copy",
                f"文案包含禁用词“{word}”",
                word,
            ))

    # 6. 违背 VI 属性篡改逻辑
    for pattern in rules.get("forbidden_appearance_patterns", []):
        match = re.search(pattern, full_text)
        if match:
            violations.append(_violation(
                "PRODUCT_APPEARANCE_CHANGE_FORBIDDEN",
                "copy",
                f"要求修改产品外观或颜色：“{match.group(0)}”",
                match.group(0),
            ))
            break

    if violations:
        result["status"] = "BLOCKED"
        result["blocked_reason"] = "；".join(item["message"] for item in violations)
    else:
        result["status"] = "PASSED"
        result["blocked_reason"] = ""
    result["violations"] = violations
    result["rules_version"] = str(rules.get("version", "unknown"))

    return result

def audit_batch(
    records: List[Dict[str, Any]],
    rules: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    rules = rules or load_rules()
    return [audit_record(r, rules) for r in records]

def main() -> None:
    if len(sys.argv) > 1:
        in_path = Path(sys.argv[1])
        records = json.loads(in_path.read_text(encoding="utf-8"))
    else:
        records = json.load(sys.stdin)

    audited = audit_batch(records)
    print(json.dumps(audited, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
