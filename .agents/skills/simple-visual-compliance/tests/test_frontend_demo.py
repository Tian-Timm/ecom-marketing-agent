from __future__ import annotations

import json
import unittest
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
INDEX_PATH = PROJECT_ROOT / "index.html"
REPORT_PATH = PROJECT_ROOT / "generated_output" / "pipeline_result.json"


class DemoMarkupParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.demo_tasks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(str(attributes["id"]))
        if tag == "button" and attributes.get("data-demo-task"):
            self.demo_tasks.append(str(attributes["data-demo-task"]))


class FrontendDemoContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = INDEX_PATH.read_text(encoding="utf-8")
        self.report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        self.records = {
            record["task_id"]: record
            for record in self.report["records"]
        }
        self.parser = DemoMarkupParser()
        self.parser.feed(self.html)

    def test_demo_has_four_representative_cases(self) -> None:
        self.assertEqual(
            self.parser.demo_tasks,
            ["MKT-001", "MKT-008", "MKT-009", "MKT-005"],
        )

    def test_demo_cases_are_backed_by_real_pipeline_results(self) -> None:
        passed = self.records["MKT-001"]
        self.assertEqual(passed["status"], "PASSED")
        self.assertTrue(passed["generated_image"])
        self.assertTrue(
            (PROJECT_ROOT / "generated_output" / passed["generated_image"]).exists()
        )

        expected_codes = {
            "MKT-008": "PRICE_BELOW_MINIMUM",
            "MKT-009": "FORBIDDEN_WORD_DETECTED",
            "MKT-005": "PRODUCT_APPEARANCE_CHANGE_FORBIDDEN",
        }
        for task_id, code in expected_codes.items():
            record = self.records[task_id]
            self.assertEqual(record["status"], "BLOCKED")
            self.assertIsNone(record["generated_image"])
            self.assertIn(code, {item["code"] for item in record["violations"]})

    def test_static_demo_is_truthful_and_local_execution_remains_available(self) -> None:
        self.assertIn("在线案例演示", self.html)
        self.assertIn("由真实流水线预生成，不在浏览器中模拟规则", self.html)
        self.assertIn('fetchJson("/api/run"', self.html)
        self.assertIn("configureRuntimeUI(apiOnline)", self.html)
        self.assertNotIn("const forbiddenWords", self.html)

    def test_generated_images_use_the_pipeline_version_to_avoid_stale_cache(self) -> None:
        self.assertIn("state.report?.generated_at || state.rulesVersion", self.html)
        self.assertIn("?v=${encodeURIComponent(assetVersion)}", self.html)
        self.assertIn("download.href = generatedImageUrl", self.html)

    def test_required_demo_ids_are_unique(self) -> None:
        required = {
            "demo-heading",
            "demo-note",
            "demo-case-list",
            "input-pane-title",
            "input-pane-note",
        }
        self.assertTrue(required.issubset(set(self.parser.ids)))
        self.assertEqual(len(self.parser.ids), len(set(self.parser.ids)))


if __name__ == "__main__":
    unittest.main()
