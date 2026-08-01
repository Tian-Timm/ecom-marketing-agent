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
        self.assertIn("电商营销素材合规与生成 Agent", self.html)
        self.assertIn("连接飞书 Base，自动读取商品资料和营销任务，完成营销素材风险审查与自动生成。", self.html)
        intro = self.html.split('<section class="intro"', 1)[1].split("</section>", 1)[0]
        self.assertNotIn('class="flow"', intro)
        self.assertNotIn("提交任务", intro)
        self.assertNotIn("合规审查", intro)
        self.assertNotIn("确定性组装", intro)
        self.assertIn("演示案例", self.html)
        self.assertIn("实时读取飞书并重新执行，本次公开体验不会修改飞书数据。", self.html)
        self.assertIn("运行 Agent", self.html)
        self.assertIn("读取数据", self.html)
        self.assertIn("字段解析", self.html)
        self.assertIn("生成或阻断", self.html)
        for label in ("正常出图", "价格违规", "文案风险", "视觉修改风险"):
            self.assertIn(f'class="demo-case-name">{label}</span>', self.html)
        for legacy_label in ("MKT-001 · 正常营销任务", "MKT-008 · 价格违规任务", "MKT-009 · 文案风险任务", "MKT-005 · 视觉修改风险"):
            self.assertNotIn(legacy_label, self.html)
        self.assertIn('class="demo-case-id">MKT-001</span>', self.html)
        self.assertIn('button.setAttribute("aria-pressed", String(active))', self.html)
        self.assertIn("selectRecord(taskId)", self.html)
        pipeline_css = self.html.split(".demo-pipeline {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow: hidden", pipeline_css)
        self.assertNotIn("overflow-x: auto", pipeline_css)
        mobile_css = self.html.split("@media (max-width: 640px)", 1)[1]
        self.assertIn(".demo-case-list", mobile_css)
        self.assertIn("repeat(2, minmax(0, 1fr))", mobile_css)
        self.assertIn(".readonly-task-summary", mobile_css)
        self.assertIn("grid-template-columns: 1fr", mobile_css)
        self.assertIn("在线案例演示", self.html)
        self.assertIn("当前案例只读信息", self.html)
        self.assertIn('id="readonly-task-summary"', self.html)
        self.assertIn('id="editable-task-fields"', self.html)
        for summary_id in (
            "readonly-task-id",
            "readonly-img-type",
            "readonly-aspect-ratio",
            "readonly-promo-price",
            "readonly-min-price",
            "readonly-deploy-date",
            "readonly-campaign-start",
            "readonly-campaign-end",
            "readonly-main-text",
            "readonly-sub-text",
        ):
            self.assertIn(f'id="{summary_id}"', self.html)
        self.assertIn("summaryMoney", self.html)
        self.assertIn("summaryDate", self.html)
        self.assertIn("¥", self.html)
        self.assertIn("updateReadonlySummary(record)", self.html)
        self.assertIn('readonlyTaskSummary.classList.toggle("hidden", !showReadonlySummary)', self.html)
        self.assertIn('editableTaskFields.classList.toggle("hidden", showReadonlySummary)', self.html)
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
        self.assertNotIn('id="datasource-onboarding"', self.html)
        self.assertNotIn('id="datasource-access-button"', self.html)
        self.assertIn('id="onboarding-panel"', self.html)
        self.assertIn('id="discover-button"', self.html)
        self.assertIn('id="confirm-button"', self.html)
        self.assertIn('id="activate-button"', self.html)
        self.assertIn('id="admin-form"', self.html)
        self.assertIn("adminDialog.showModal()", self.html)
        self.assertIn('fetchJson("/api/sources"', self.html)
        self.assertIn('class="onboarding-hint"', self.html)
        self.assertIn("支持飞书 Base 字段发现、语义映射、dry-run 与版本化激活。", self.html)

    def test_generated_images_use_the_pipeline_version_to_avoid_stale_cache(self) -> None:
        self.assertIn("state.report?.generated_at || state.rulesVersion", self.html)
        self.assertIn("?v=${encodeURIComponent(assetVersion)}", self.html)
        self.assertIn("download.href = generatedImageUrl", self.html)

    def test_workspace_prioritizes_result_panel_responsively(self) -> None:
        self.assertIn(
            "grid-template-columns: minmax(190px, 0.45fr) minmax(340px, 0.8fr) minmax(500px, 1.35fr);",
            self.html,
        )
        tablet_css = self.html.split("@media (max-width: 1240px)", 1)[1].split(
            "@media (max-width: 840px)", 1
        )[0]
        self.assertIn("grid-template-columns: 220px minmax(340px, 1fr);", tablet_css)
        self.assertIn(".result-pane", tablet_css)
        self.assertIn("grid-column: 1 / -1", tablet_css)

        mobile_css = self.html.split("@media (max-width: 640px)", 1)[1]
        self.assertIn("grid-template-columns: 1fr;", mobile_css)
        self.assertIn(".preview", mobile_css)
        self.assertIn("object-fit: contain", self.html)
        self.assertIn("width: 100%", self.html)
        self.assertIn("max-height: min(520px, 60vh)", self.html)
        self.assertIn('details.open = false;', self.html)
        self.assertIn('panel.open = false;', self.html)

    def test_system_summary_merges_metrics_and_datasource_state(self) -> None:
        self.assertIn('id="system-summary-bar"', self.html)
        self.assertNotIn('class="metrics"', self.html)
        self.assertNotIn('class="source-strip"', self.html)
        for element_id in (
            "metric-products",
            "metric-total",
            "metric-passed",
            "metric-blocked",
            "metric-images",
            "source-name",
            "source-mode",
            "semantic-mode",
            "public-mode",
            "last-sync",
        ):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn('id="source-select-cell"', self.html)
        self.assertIn('id="source-select"', self.html)
        self.assertIn("function updateMetrics()", self.html)
        self.assertIn("function configureRuntimeUI(runtime)", self.html)
        self.assertIn('publicMode.textContent = state.isAdminMode ? "管理员模式" : "公开只读";', self.html)
        self.assertIn('setText("source-mode", apiOnline ? "飞书实时读取" : "静态快照");', self.html)
        summary_css = self.html.split(".system-summary-bar {", 1)[1].split("}", 1)[0]
        self.assertIn("flex-wrap: wrap", summary_css)
        self.assertNotIn("overflow-x", summary_css)

    def test_demo_exposes_pipeline_and_feishu_delivery_evidence(self) -> None:
        self.assertEqual(self.report["schema_version"], "2.0")
        self.assertTrue(self.report["pipeline_version"])
        self.assertEqual(self.report["execution_mode"], "static_snapshot")
        self.assertFalse(self.report["writeback"])
        sync = self.report["feishu_sync"]
        self.assertEqual(sync["records_read"], 10)
        self.assertEqual(sync["records_written"], 0)
        self.assertEqual(sync["images_uploaded"], 0)
        self.assertEqual(sync["unchanged_skipped"], 0)
        for record in self.report["records"]:
            self.assertTrue(record["input_hash"])
            self.assertGreaterEqual(record["duration_ms"], 0)
            steps = {
                step["name"]: step
                for step in record["execution_trace"]
            }
            self.assertEqual(steps["semantic_review"]["status"], "SKIPPED")
            self.assertEqual(steps["delivery"]["status"], "SKIPPED")
            self.assertNotIn("sync_status", record)
        self.assertIn("查看处理记录", self.html)
        self.assertIn("复制 JSON", self.html)
        self.assertIn("REVIEW_REQUIRED", self.html)
        self.assertIn("FAILED", self.html)

    def test_public_evidence_summary_is_truthful_and_sanitized(self) -> None:
        evidence_path = PROJECT_ROOT / "generated_output" / "public_evidence.json"
        self.assertTrue(evidence_path.exists())
        data = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(data["tests"]["passed"], 85)
        self.assertEqual(data["tests"]["total"], 85)
        self.assertEqual(data["evaluation"]["status_correct"], 10)
        self.assertEqual(data["evaluation"]["anomalies_detected"], 6)
        self.assertGreater(data["e2e"]["passed_duration_seconds"], 0)
        self.assertGreater(data["e2e"]["blocked_duration_seconds"], 0)
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
            "demo-case-list",
            "input-pane-title",
            "input-pane-note",
            "readonly-task-summary",
            "editable-task-fields",
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


    def test_dual_mode_frontend_security_and_boundaries(self) -> None:
        self.assertIn("/api/demo_run", self.html)
        self.assertIn('action: "run_task"', self.html)
        self.assertNotIn('action: "run_next_pending"', self.html)
        self.assertIn('id="mode-status"', self.html)
        self.assertIn('id="admin-mode-toggle"', self.html)
        self.assertIn("数据源接入", self.html)
        self.assertIn('id="source-select-cell"', self.html)
        self.assertIn('class="source-cell hidden"', self.html)
        self.assertNotIn("localStorage", self.html)
        self.assertNotIn("sessionStorage", self.html)
        self.assertIn('state.adminToken = ""', self.html)
        self.assertIn('state.sourceId = ""', self.html)
        self.assertIn("公开演示模式", self.html)
        self.assertIn("数据源接入", self.html)
        self.assertNotIn(">管理员模式<", self.html)


if __name__ == "__main__":
    unittest.main()
