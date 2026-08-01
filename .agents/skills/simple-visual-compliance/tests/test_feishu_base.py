from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from feishu_base_adapter import (
    LarkCliAdapter,
    build_task_input,
    records_from_envelope,
    restore_legacy_input,
    scalar_cell_value,
    update_fields_for_result,
)


class FeishuBaseAdapterTests(unittest.TestCase):
    def test_cli_metadata_rejects_incomplete_pages(self) -> None:
        class PagingCli(LarkCliAdapter):
            def run(self, args):
                return {"ok": True, "data": {"items": [], "has_more": True}}

        with self.assertRaisesRegex(RuntimeError, "未完整返回"):
            PagingCli().list_tables("base")
        with self.assertRaisesRegex(RuntimeError, "未完整返回"):
            PagingCli().list_fields("base", "table")

    def test_single_select_cell_is_normalized_for_idempotency(self):
        self.assertEqual(scalar_cell_value(["审查通过"]), "审查通过")
        self.assertEqual(scalar_cell_value("70ef6671"), "70ef6671")

    def test_records_from_envelope_keeps_record_ids(self) -> None:
        records = records_from_envelope({
            "ok": True,
            "data": {
                "fields": ["任务ID", "主文案"],
                "data": [["MKT-001", "城市轻装"]],
                "record_id_list": ["rec001"],
                "has_more": False,
            },
        })
        self.assertEqual(records[0]["任务ID"], "MKT-001")
        self.assertEqual(records[0]["_record_id"], "rec001")

    def test_partial_page_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "不完整数据"):
            records_from_envelope({
                "ok": True,
                "data": {
                    "fields": [],
                    "data": [],
                    "record_id_list": [],
                    "has_more": True,
                },
            })

    def test_legacy_output_is_restored_to_real_input(self) -> None:
        restored = restore_legacy_input(
            {"任务ID": "MKT-005", "补充要求": "[已完成] 旧输出"},
            {"MKT-005": {"补充要求": "需将杯身改成粉红色匹配氛围"}},
        )
        self.assertEqual(restored["补充要求"], "需将杯身改成粉红色匹配氛围")
        self.assertTrue(restored["_legacy_input_restored"])

    def test_task_input_uses_product_price_floor_and_excludes_outputs(self) -> None:
        task_input = build_task_input(
            {
                "任务ID": "MKT-001",
                "主文案": "城市轻装",
                "审查状态": "审查通过",
                "_record_id": "rec001",
            },
            {"最低允许促销价": 89, "商品名称": "CHA CUP 保温杯"},
        )
        self.assertEqual(task_input["最低允许促销价"], 89)
        self.assertNotIn("审查状态", task_input)
        self.assertNotIn("_record_id", task_input)

    def test_result_fields_do_not_write_back_into_main_copy(self) -> None:
        fields = update_fields_for_result(
            {
                "task_id": "MKT-008",
                "status": "BLOCKED",
                "violations": [{"message": "活动价低于最低允许价格"}],
                "input_hash": "abc123",
                "rules_version": "rules-1",
                "pipeline_version": "pipeline-1",
            },
            image_url=None,
            sub_text="价格字号需要明显",
            processed_at="2026-07-31 14:30:00",
            output_fields={
                "status": "审查状态",
                "issues": "问题说明",
                "image_url": "生成图片链接",
                "processed_at": "处理时间",
                "input_hash": "输入指纹",
                "pipeline_version": "处理版本",
            },
        )
        self.assertEqual(fields["补充要求"], "价格字号需要明显")
        self.assertEqual(fields["审查状态"], "需修改")
        self.assertEqual(fields["问题说明"], "活动价低于最低允许价格")
        self.assertIsNone(fields["生成图片链接"])


if __name__ == "__main__":
    unittest.main()
