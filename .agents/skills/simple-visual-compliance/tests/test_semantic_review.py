from __future__ import annotations

import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from semantic_review import validate_semantic_result


class SemanticReviewContractTests(unittest.TestCase):
    def test_valid_pass_result_is_normalized(self) -> None:
        result = validate_semantic_result({
            "status": "PASSED",
            "violations": [],
            "confidence": 0.97,
            "summary": "未发现语义风险。",
            "model": "deepseek-chat",
        })
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["violations"], [])

    def test_unknown_rule_code_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_semantic_result({
                "status": "BLOCKED",
                "violations": [{
                    "code": "MODEL_INVENTED_RULE",
                    "field": "copy",
                    "message": "未知规则",
                    "evidence": "示例",
                }],
                "confidence": 0.8,
            })

    def test_passed_result_cannot_hide_violations(self) -> None:
        with self.assertRaises(ValueError):
            validate_semantic_result({
                "status": "PASSED",
                "violations": [{
                    "code": "SEMANTIC_FORBIDDEN_CLAIM",
                    "field": "main_text",
                    "message": "存在风险",
                    "evidence": "全行业领先",
                }],
                "confidence": 0.8,
            })


if __name__ == "__main__":
    unittest.main()
