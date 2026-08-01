from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import web_api
import online_runtime


class PublicDemoRuntimeTests(unittest.TestCase):
    def test_public_task_reads_reviews_and_renders_without_writeback(self) -> None:
        import run_pipeline

        config = {
            "name": "Demo Base",
            "url": "https://feishu.example/base/demo",
            "base_token": "base-token",
            "product_table": {"id": "tbl-products"},
            "task_table": {"id": "tbl-tasks"},
            "legacy_input_fixture": "fixture.csv",
        }
        product = {"商品名称": "CHA CUP", "最低允许促销价": 89}
        task = {
            "任务ID": "MKT-001",
            "图片类型": "电商主图",
            "画布比例": "1:1",
            "投放日期": "2026-07-25",
            "活动名称": "日常商品展示",
            "活动价": 129,
            "活动开始日期": "2026-07-25",
            "活动结束日期": "2026-08-31",
            "主文案": "城市轻装 随时出发",
            "补充要求": "白底展示产品主体",
            "审查状态": "审查通过",
            "输入指纹": "same-as-before",
            "处理版本": "old-version",
            "_record_id": "rec-001",
        }
        adapter = MagicMock()
        adapter.list_records.side_effect = lambda _base, table_id: (
            [product] if table_id == "tbl-products" else [task]
        )
        reviewer = MagicMock()
        reviewer.review.return_value = {
            "status": "PASSED",
            "violations": [],
            "confidence": 1.0,
            "summary": "语义复核通过",
        }

        def fake_assemble(records, output_dir: Path):
            result = dict(records[0])
            result["generated_image"] = "MKT-001_rendered.png"
            result["artifact"] = {"filename": result["generated_image"]}
            self.assertTrue(Path(output_dir).is_relative_to(Path(tempfile.gettempdir())))
            return [result]

        with patch.dict(os.environ, {"PUBLIC_DEMO_TASK_IDS": "MKT-001"}, clear=False), \
                patch.object(online_runtime, "load_config", return_value=config), \
                patch.object(online_runtime, "load_fixture_inputs", return_value={}), \
                patch.object(online_runtime.FeishuOpenApiAdapter, "from_env", return_value=adapter), \
                patch.object(online_runtime.DeepSeekSemanticReviewer, "from_env", return_value=reviewer), \
                patch.object(online_runtime, "load_rules", return_value={"version": "rules-test"}), \
                patch.object(run_pipeline, "audit_batch", wraps=run_pipeline.audit_batch) as audit_mock, \
                patch.object(run_pipeline, "assemble_batch", side_effect=fake_assemble) as assemble_mock, \
                patch.object(online_runtime, "runtime_status", return_value={"online": True}) as runtime_mock:
            report = web_api.run_public_demo_task("MKT-001")

        self.assertEqual((report["execution_mode"], report["writeback"]), ("live_readonly", False))
        record = report["records"][0]
        self.assertNotEqual(record.get("sync_status"), "SKIPPED_UNCHANGED")
        self.assertNotIn("SKIPPED_UNCHANGED", str(record))
        adapter.list_records.assert_any_call("base-token", "tbl-products")
        adapter.list_records.assert_any_call("base-token", "tbl-tasks")
        audit_mock.assert_called_once()
        reviewer.review.assert_called_once()
        assemble_mock.assert_called_once()
        runtime_mock.assert_called_once()
        adapter.upload_image.assert_not_called()
        adapter.batch_update.assert_not_called()

    def test_public_task_rejects_task_outside_environment_whitelist(self) -> None:
        with patch.dict(os.environ, {"PUBLIC_DEMO_TASK_IDS": "MKT-001"}, clear=False), \
                patch.object(online_runtime.FeishuOpenApiAdapter, "from_env") as adapter_factory:
            with self.assertRaises(KeyError):
                web_api.run_public_demo_task("MKT-999")

        adapter_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
