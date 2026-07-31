from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_DIR.parents[2]
SCRIPTS_DIR = SKILL_DIR / "scripts"
STANDARDIZER_DIR = (
    PROJECT_ROOT / ".agents" / "skills" / "dataset-standardizer" / "scripts"
)
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(STANDARDIZER_DIR))

from feishu_openapi_adapter import FeishuOpenApiAdapter
from online_runtime import image_token_belongs_to_demo_base, live_report
from src import web_api


class FakeHeaders(dict):
    def get(self, key: str, default: str = "") -> str:
        return str(super().get(key, default))


class FakeHandler:
    def __init__(self, token: str) -> None:
        self.headers = FakeHeaders({
            "X-Demo-Admin-Token": token,
            "X-Forwarded-For": "203.0.113.8",
        })
        self.client_address = ("203.0.113.8", 443)


class RecordingOpenApiAdapter(FeishuOpenApiAdapter):
    def __init__(self) -> None:
        super().__init__("app-id", "app-secret")
        self.calls: list[tuple[str, str, dict | None]] = []

    def _json_request(self, method, path, *, body=None, query=None):
        self.calls.append((method, path, body))
        if method == "GET" and "product" in path:
            return {
                "code": 0,
                "data": {
                    "items": [{
                        "record_id": "product-record",
                        "fields": {
                            "商品名称": "CHA CUP 城市轻量保温杯",
                            "最低允许促销价": 89,
                        },
                    }],
                    "has_more": False,
                },
            }
        return {
            "code": 0,
            "data": {
                "items": [{
                    "record_id": "task-record",
                    "fields": {
                        "任务ID": "MKT-001",
                        "图片类型": "电商主图",
                        "画布比例": "1:1",
                        "活动价": 129,
                        "投放日期": "2026-07-25",
                        "活动开始日期": "2026-07-25",
                        "活动结束日期": "2026-08-31",
                        "主文案": "城市轻装 随时出发",
                        "补充要求": "白底展示产品主体",
                        "审查状态": "审查通过",
                        "输入指纹": "70ef6671a24ca687",
                    },
                }],
                "has_more": False,
            },
        }


class OnlineApiTests(unittest.TestCase):
    def setUp(self) -> None:
        with web_api._state_lock:
            web_api._requests_by_ip.clear()
            web_api._running_tasks.clear()

    def test_write_requires_matching_admin_token(self) -> None:
        with patch.dict(os.environ, {"DEMO_ADMIN_TOKEN": "correct"}, clear=False):
            with self.assertRaises(PermissionError):
                web_api._authorize_write(FakeHandler("wrong"))
            web_api._authorize_write(FakeHandler("correct"))

    def test_live_report_maps_fixed_base_records(self) -> None:
        adapter = RecordingOpenApiAdapter()
        original = adapter.list_records

        def list_records(base_token: str, table_id: str):
            marker = "product" if table_id == "tblVd37g9T0cdOTR" else "task"
            payload = adapter._json_request(
                "GET",
                f"/{marker}",
                query={"page_size": 500},
            )
            records = []
            for item in payload["data"]["items"]:
                record = dict(item["fields"])
                record["_record_id"] = item["record_id"]
                records.append(record)
            return records

        adapter.list_records = list_records
        report = live_report(adapter)
        self.assertEqual(report["summary"]["total"], 1)
        self.assertEqual(report["records"][0]["status"], "PASSED")
        self.assertEqual(
            report["records"][0]["feishu_record_id"],
            "task-record",
        )
        adapter.list_records = original

    def test_multipart_upload_contains_bitable_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image = Path(temp_dir) / "result.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nsample")
            body, content_type_boundary = FeishuOpenApiAdapter._multipart_body(
                {
                    "file_name": "result.png",
                    "parent_type": "bitable_file",
                    "parent_node": "base-token",
                    "size": str(image.stat().st_size),
                },
                image,
            )
        self.assertIn(b"bitable_file", body)
        self.assertIn(b"base-token", body)
        self.assertIn(b"sample", body)
        self.assertTrue(content_type_boundary.startswith("----cha-cup-"))

    def test_image_proxy_only_accepts_fixed_base_attachment(self) -> None:
        adapter = RecordingOpenApiAdapter()

        def list_records(base_token: str, table_id: str):
            self.assertEqual(base_token, "SfsSb7Tw2aeiQJsmQTlczjX7nyN")
            return [{
                "任务ID": "MKT-001",
                "生成图片": [{"file_token": "allowed-token"}],
            }]

        adapter.list_records = list_records
        self.assertTrue(
            image_token_belongs_to_demo_base("allowed-token", adapter)
        )
        self.assertFalse(
            image_token_belongs_to_demo_base("other-token", adapter)
        )


if __name__ == "__main__":
    unittest.main()
