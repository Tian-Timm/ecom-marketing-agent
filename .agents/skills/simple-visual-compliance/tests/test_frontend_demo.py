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
        self.assertIn("查看预置案例的输入信息", self.html)
        self.assertIn('fetchJson("/api/run"', self.html)
        self.assertIn("configureRuntimeUI(status)", self.html)
        self.assertIn("X-Demo-Admin-Token", self.html)
        self.assertIn("飞书多维表格", self.html)
        self.assertNotIn("DEEPSEEK_API_KEY", self.html)
        self.assertNotIn("FEISHU_APP_SECRET", self.html)
        self.assertNotIn("const forbiddenWords", self.html)

    def test_configurable_source_onboarding_is_dynamic_and_keeps_legacy_fallback(self) -> None:
        # Every field of the selected table remains manually selectable; discovery
        # recommendations merely move confidence-labelled options to the top.
        self.assertIn("tableFields.filter((field) => !recommended.has(field.field_id))", self.html)
        self.assertIn("未推荐/需人工确认", self.html)
        self.assertIn("candidate.confidence === \"LOW\"", self.html)
        # Output fields have no independent table picker: they are refreshed from
        # the selected task table before confirm.
        self.assertIn("groups.writeback.dataset.tableId = table.value", self.html)
        self.assertIn("writeback_table_id: writeback.dataset.tableId", self.html)
        self.assertNotIn('table.dataset.tableRole = "writeback"', self.html)
        for endpoint in ("/api/discover", "/api/confirm", "/api/activate"):
            self.assertIn(endpoint, self.html)
        self.assertIn('fetch(record.source_image_url, {headers: {"X-Demo-Admin-Token": state.adminToken}})', self.html)
        self.assertIn('state.sourceId ? `/api/tasks?source_id=', self.html)
        onboarding = self.html.split('id="onboarding-panel"', 1)[1].split("</section>", 1)[0]
        self.assertNotIn("secret", onboarding.lower())

    def test_generated_images_use_the_pipeline_version_to_avoid_stale_cache(self) -> None:
        self.assertIn("state.report?.generated_at || state.rulesVersion", self.html)
        self.assertIn("?v=${encodeURIComponent(assetVersion)}", self.html)
        self.assertIn("download.href = generatedImageUrl", self.html)

    def test_demo_exposes_pipeline_and_feishu_delivery_evidence(self) -> None:
        self.assertEqual(self.report["schema_version"], "2.0")
        self.assertTrue(self.report["pipeline_version"])
        self.assertIn("/base/", self.report["source"])
        sync = self.report["feishu_sync"]
        self.assertEqual(sync["records_read"], 10)
        self.assertIn(sync["records_written"], {0, 10})
        self.assertIn(sync["images_uploaded"], {0, 4})
        self.assertIn(sync["unchanged_skipped"], {0, 10})
        for record in self.report["records"]:
            self.assertTrue(record["input_hash"])
            self.assertGreaterEqual(record["duration_ms"], 0)
            steps = {
                step["name"]: step
                for step in record["execution_trace"]
            }
            self.assertEqual(steps["semantic_review"]["status"], "NOT_CONFIGURED")
            self.assertIn(steps["delivery"]["status"], {"COMPLETED", "SKIPPED"})
            self.assertIn(record["sync_status"], {"COMPLETED", "SKIPPED_UNCHANGED"})
        self.assertIn("查看处理记录", self.html)
        self.assertIn("复制 JSON", self.html)
        self.assertIn("REVIEW_REQUIRED", self.html)
        self.assertIn("FAILED", self.html)

    def test_public_evidence_summary_is_truthful_and_sanitized(self) -> None:
        evidence_path = PROJECT_ROOT / "generated_output" / "public_evidence.json"
        self.assertTrue(evidence_path.exists())
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(data["tests"]["passed"], 30)
        self.assertEqual(data["tests"]["total"], 30)
        self.assertEqual(data["evaluation"]["status_correct"], 10)
        self.assertEqual(data["evaluation"]["anomalies_detected"], 6)
        self.assertEqual(data["e2e"]["passed_duration_seconds"], 10.68)
        self.assertEqual(data["e2e"]["blocked_duration_seconds"], 6.28)
        self.assertIn("10 条", data["sample_note"])

        content_str = json.dumps(data)
        self.assertNotIn("FEISHU_APP_SECRET", content_str)
        self.assertNotIn("DEEPSEEK_API_KEY", content_str)
        self.assertNotIn("file_token", content_str)

        self.assertIn("evidence-section", self.html)
        self.assertIn("loadEvidence()", self.html)

    def test_required_demo_ids_are_unique(self) -> None:
        required = {
            "demo-heading",
            "demo-note",
            "demo-case-list",
            "input-pane-title",
            "input-pane-note",
            "base-link",
            "semantic-mode",
            "last-sync",
            "admin-dialog",
            "evidence-section",
            "ev-tests",
            "ev-status",
            "ev-anomalies",
            "ev-e2e",
            "ev-note",
        }
        self.assertTrue(required.issubset(set(self.parser.ids)))
        self.assertEqual(len(self.parser.ids), len(set(self.parser.ids)))


if __name__ == "__main__":
    unittest.main()
