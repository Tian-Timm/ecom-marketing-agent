"""M2 风控与合规检验引擎 - 确定性规则 + DeepSeek AI 兜底双重风控"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .config import (
    ALLOWED_ASPECT_RATIOS,
    DEEPSEEK_API_KEY,
    FORBIDDEN_WORDS,
    MIN_ALLOW_PROMO_PRICE,
)

COLOR_CHANGE_RE = re.compile(r"改成.*色|改色|换色|变成.*色|改杯型|改杯盖|变色")


@dataclass
class GuardrailResult:
    task_id: str
    status: str  # "PASS" | "BLOCK"
    reasons: list[str] = field(default_factory=list)
    ai_checked: bool = False
    ai_details: str = ""

    @property
    def is_blocked(self) -> bool:
        return self.status == "BLOCK"

    @property
    def reason_summary(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "合规通过"


def check_deterministic_rules(task: dict[str, Any], product: dict[str, Any]) -> tuple[bool, list[str]]:
    """执行 6 大确定性风控规则检查。"""
    reasons: list[str] = []

    # 1. 关键字段缺失检查
    price = task.get("活动价")
    if price is None or str(price).strip() == "":
        reasons.append("活动价缺失")

    main_copy = task.get("主文案")
    if not main_copy or not str(main_copy).strip():
        reasons.append("主文案缺失")

    # 2. 活动价低于允许下限
    if price is not None:
        try:
            num_price = float(price)
            if num_price < MIN_ALLOW_PROMO_PRICE:
                reasons.append(f"活动价 {num_price} 元低于最低允许下限 {MIN_ALLOW_PROMO_PRICE} 元")
        except (ValueError, TypeError):
            reasons.append("活动价数值格式非法")

    # 3. 投放日期超出活动周期
    deploy_str = str(task.get("投放日期") or "").strip()
    end_str = str(task.get("活动结束日期") or "").strip()
    start_str = str(task.get("活动开始日期") or "").strip()
    if deploy_str and end_str:
        try:
            deploy_date = date.fromisoformat(deploy_str)
            end_date = date.fromisoformat(end_str)
            if deploy_date > end_date:
                reasons.append(f"投放日期 {deploy_str} 超出活动结束日期 {end_str}")
            if start_str:
                start_date = date.fromisoformat(start_str)
                if deploy_date < start_date:
                    reasons.append(f"投放日期 {deploy_str} 早于活动开始日期 {start_str}")
        except ValueError:
            reasons.append("日期格式解析异常")

    # 4. 广告法禁用词检查
    full_text = f"{main_copy or ''} {task.get('补充要求') or ''}"
    for forbidden in FORBIDDEN_WORDS:
        if forbidden in full_text:
            reasons.append(f"文案包含禁用词: 『{forbidden}』")

    # 5. 画布比例合法性检查
    ratio = str(task.get("画布比例") or "").strip()
    if ratio not in ALLOWED_ASPECT_RATIOS:
        reasons.append(f"画布比例 {ratio} 不在允许范围 {list(ALLOWED_ASPECT_RATIOS.keys())}")

    # 6. 改色 / 改型违规检查
    extra = str(task.get("补充要求") or "").strip()
    if COLOR_CHANGE_RE.search(extra):
        reasons.append(f"要求改色/改型违规: 『{extra}』")

    passed = len(reasons) == 0
    return passed, reasons


def check_ai_compliance(task: dict[str, Any]) -> tuple[bool, str]:
    """调用 DeepSeek AI 模型对软性文本做辅助合规判断。"""
    if not DEEPSEEK_API_KEY:
        return True, "DeepSeek API Key 未配置，跳过 AI 复核"

    prompt = (
        f"请审核以下出图任务的合规性：\n"
        f"- 任务ID: {task.get('任务ID')}\n"
        f"- 画布比例: {task.get('画布比例')}\n"
        f"- 活动价: {task.get('活动价')}\n"
        f"- 主文案: {task.get('主文案')}\n"
        f"- 补充要求: {task.get('补充要求')}\n"
    )

    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个广告合规与 VI 审核助手。规则：\n"
                    "1. 严禁使用极限词（全网第一、绝对保温、100%不漏等）；\n"
                    "2. 最低价不得低于89元；\n"
                    "3. 比例仅限 1:1 和 3:4；\n"
                    "4. 不得请求修改杯身颜色或形状。\n"
                    "请输出 JSON 格式: {\"compliant\": true/false, \"reason\": \"原因说明\"}"
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 200,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            # 解析 JSON 响应
            parsed = json.loads(content[content.find("{"):content.rfind("}") + 1])
            is_ok = bool(parsed.get("compliant", True))
            reason = str(parsed.get("reason", "AI 判定通过"))
            return is_ok, reason
    except Exception as exc:
        return True, f"AI 调用跳过/网络波动: {exc}"


def evaluate_task(task: dict[str, Any], product: dict[str, Any], use_ai: bool = True) -> GuardrailResult:
    """综合确定性规则 + AI 对单条任务进行风控评估。"""
    task_id = str(task.get("任务ID") or "UNKNOWN")
    passed, reasons = check_deterministic_rules(task, product)

    ai_checked = False
    ai_details = ""

    # 对于通过了确定性规则的任务，或者特殊任务进行 AI 抽查复核
    if passed and use_ai:
        ai_ok, ai_reason = check_ai_compliance(task)
        ai_checked = True
        ai_details = ai_reason
        if not ai_ok:
            passed = False
            reasons.append(f"AI 拦截: {ai_reason}")

    status = "PASS" if passed else "BLOCK"
    return GuardrailResult(
        task_id=task_id,
        status=status,
        reasons=reasons,
        ai_checked=ai_checked,
        ai_details=ai_details,
    )
