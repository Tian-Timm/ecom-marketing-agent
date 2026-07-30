from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_DIR.parents[2]
SCRIPTS_DIR = SKILL_DIR / "scripts"
STANDARDIZER_DIR = PROJECT_ROOT / ".agents" / "skills" / "dataset-standardizer" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(STANDARDIZER_DIR))

from audit_text import audit_batch, load_rules
from assemble_image import assemble_batch
from run_pipeline import run_pipeline, write_report


def valid_record(**changes: object) -> dict:
    record = {
        "task_id": "TEST-001",
        "img_type": "电商主图",
        "aspect_ratio": "1:1",
        "deploy_date": "2026-08-15",
        "campaign_start": "2026-08-01",
        "campaign_end": "2026-08-31",
        "promo_price": 99,
        "min_price": 89,
        "main_text": "城市轻装 随时出发",
        "sub_text": "突出活动价",
    }
    record.update(changes)
    return record


class RuleAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = load_rules()

    def audit(self, **changes: object) -> dict:
        return audit_batch([valid_record(**changes)], rules=self.rules)[0]

    def test_valid_record_passes_with_its_own_campaign_period(self) -> None:
        result = self.audit()
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["violations"], [])

    def test_normal_word_about_improvement_is_not_treated_as_recoloring(self) -> None:
        result = self.audit(main_text="我们改进了杯盖体验")
        self.assertEqual(result["status"], "PASSED")

    def test_explicit_recoloring_is_blocked(self) -> None:
        result = self.audit(sub_text="把杯身改成粉红色")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(
            "PRODUCT_APPEARANCE_CHANGE_FORBIDDEN",
            {item["code"] for item in result["violations"]},
        )

    def test_invalid_date_format_is_blocked(self) -> None:
        result = self.audit(deploy_date="2026-07-3")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn(
            "DEPLOY_DATE_FORMAT_INVALID",
            {item["code"] for item in result["violations"]},
        )

    def test_stale_image_is_removed_for_blocked_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            stale = output_dir / "TEST-001_rendered.png"
            stale.write_bytes(b"stale")
            blocked = self.audit(promo_price=69)
            result = assemble_batch([blocked], output_dir)[0]
            self.assertFalse(stale.exists())
            self.assertIsNone(result["generated_image"])


class PipelineTests(unittest.TestCase):
    def test_fixture_pipeline_produces_four_images_and_six_blocks(self) -> None:
        input_path = SKILL_DIR / "assets" / "marketing_tasks.csv"
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            report = run_pipeline(input_path, output_dir)
            report_path = write_report(report, output_dir)
            self.assertEqual(report["summary"]["passed"], 4)
            self.assertEqual(report["summary"]["blocked"], 6)
            self.assertEqual(len(list(output_dir.glob("*_rendered.png"))), 4)
            self.assertTrue(report_path.exists())
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["schema_version"], "1.0")
            blocked = [item for item in saved["records"] if item["status"] == "BLOCKED"]
            self.assertTrue(all(item["generated_image"] is None for item in blocked))


if __name__ == "__main__":
    unittest.main()
