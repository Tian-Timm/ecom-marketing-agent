#!/usr/bin/env python3
"""DeepSeek 语义复核适配器，只返回审查结论，不修改业务输入。"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Dict

ALLOWED_STATUSES = {"PASSED", "BLOCKED", "REVIEW_REQUIRED"}
SEMANTIC_CODES = {
    "SEMANTIC_FORBIDDEN_CLAIM",
    "SEMANTIC_PRODUCT_APPEARANCE_CHANGE",
    "SEMANTIC_AMBIGUOUS_REQUIREMENT",
}

SYSTEM_PROMPT = """你是电商营销素材的语义合规复核员。
只判断三类风险：
1. 广告禁用词的变体、近义表达或无法证明的绝对化承诺；
2. 修改商品颜色、结构、Logo、包装或产品外观的意图；
3. 信息含糊到无法稳定判断，需要人工复核。

禁止修改、补全或推断输入字段。确定性价格、日期和画布规则不属于你的职责。
必须返回 JSON 对象，结构如下：
{
  "status": "PASSED | BLOCKED | REVIEW_REQUIRED",
  "violations": [
    {
      "code": "SEMANTIC_FORBIDDEN_CLAIM | SEMANTIC_PRODUCT_APPEARANCE_CHANGE | SEMANTIC_AMBIGUOUS_REQUIREMENT",
      "field": "main_text | sub_text",
      "message": "面向业务人员的简短原因",
      "evidence": "输入中的原文证据"
    }
  ],
  "confidence": 0.0,
  "summary": "一句话复核结论"
}
若没有风险，violations 必须为空。不要输出 JSON 之外的内容。"""


class DeepSeekSemanticReviewer:
    """通过一个稳定的 review 接口隐藏 DeepSeek HTTP、重试与结果校验。"""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 12.0,
        attempts: int = 2,
    ) -> None:
        if not api_key:
            raise ValueError("DeepSeek API 密钥不能为空")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.attempts = max(1, attempts)

    @classmethod
    def from_env(cls) -> "DeepSeekSemanticReviewer | None":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            return None
        return cls(
            api_key,
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip()
            or "deepseek-chat",
            base_url=os.environ.get(
                "DEEPSEEK_BASE_URL",
                "https://api.deepseek.com",
            ).strip()
            or "https://api.deepseek.com",
            timeout_seconds=float(
                os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "12")
            ),
        )

    def _request(self, record: Dict[str, Any]) -> Dict[str, Any]:
        user_payload = {
            "task_id": record.get("task_id"),
            "product_name": record.get("product_name"),
            "main_text": record.get("main_text"),
            "sub_text": record.get("sub_text"),
        }
        request_body = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(user_payload, ensure_ascii=False),
                    },
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 600,
                "temperature": 0.0,
                "stream": False,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=request_body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "cha-cup-compliance/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(
            request,
            timeout=self.timeout_seconds,
        ) as response:
            envelope = json.loads(response.read().decode("utf-8"))
        content = envelope["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        parsed["model"] = str(envelope.get("model") or self.model)
        usage = envelope.get("usage")
        if isinstance(usage, dict):
            parsed["usage"] = usage
        return parsed

    def review(self, record: Dict[str, Any]) -> Dict[str, Any]:
        last_error = "语义复核失败"
        for _ in range(self.attempts):
            try:
                return validate_semantic_result(self._request(record))
            except (
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                socket.timeout,
                TimeoutError,
                urllib.error.URLError,
            ) as exc:
                last_error = str(exc) or exc.__class__.__name__
        return {
            "status": "REVIEW_REQUIRED",
            "violations": [
                {
                    "code": "SEMANTIC_AMBIGUOUS_REQUIREMENT",
                    "field": "copy",
                    "message": "语义复核暂时无法完成，任务已转人工复核",
                    "evidence": "",
                }
            ],
            "confidence": 0.0,
            "summary": "语义服务不可用，未默认放行。",
            "model": self.model,
            "error": {
                "code": "SEMANTIC_REVIEW_UNAVAILABLE",
                "message": last_error,
            },
        }


def validate_semantic_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("语义复核结果必须是 JSON 对象")
    status = str(payload.get("status") or "")
    if status not in ALLOWED_STATUSES:
        raise ValueError("语义复核返回了未知状态")
    violations = payload.get("violations")
    if not isinstance(violations, list):
        raise ValueError("语义复核 violations 必须是数组")

    normalized = []
    for item in violations:
        if not isinstance(item, dict):
            raise ValueError("语义复核违规项格式错误")
        code = str(item.get("code") or "")
        if code not in SEMANTIC_CODES:
            raise ValueError("语义复核返回了未知规则码")
        normalized.append(
            {
                "code": code,
                "field": str(item.get("field") or "copy"),
                "message": str(item.get("message") or "语义审查发现风险"),
                "value": str(item.get("evidence") or ""),
                "source": "deepseek",
            }
        )

    confidence = float(payload.get("confidence", 0.0))
    if not 0 <= confidence <= 1:
        raise ValueError("语义复核 confidence 必须在 0 到 1 之间")
    if status == "PASSED" and normalized:
        raise ValueError("PASSED 状态不能包含违规项")
    if status == "BLOCKED" and not normalized:
        raise ValueError("BLOCKED 状态必须包含违规项")

    return {
        "status": status,
        "violations": normalized,
        "confidence": confidence,
        "summary": str(payload.get("summary") or ""),
        "model": str(payload.get("model") or "deepseek-chat"),
        **({"usage": payload["usage"]} if isinstance(payload.get("usage"), dict) else {}),
    }
