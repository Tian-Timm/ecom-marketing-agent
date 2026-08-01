from __future__ import annotations

import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import web_api


class FakeHandler:
    def __init__(
        self,
        *,
        payload: dict | None = None,
        token: str | None = None,
        path: str = "/api/demo_run",
        ip: str = "198.51.100.9",
    ) -> None:
        body = json.dumps(payload or {}).encode("utf-8") if payload is not None else b""
        self.headers = {
            "Content-Length": str(len(body)),
            "X-Forwarded-For": ip,
        }
        if token:
            self.headers["X-Demo-Admin-Token"] = token
        self.rfile, self.wfile = io.BytesIO(body), io.BytesIO()
        self.client_address = (ip, 443)
        self.path = path
        self.status: int | None = None
        self.response_headers: dict[str, str] = {}

    def send_response(self, status) -> None:
        self.status = int(status)

    def send_header(self, name: str, value: str) -> None:
        self.response_headers[name] = value

    def end_headers(self) -> None:
        pass

    @property
    def json(self) -> dict:
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class PublicDemoApiTests(unittest.TestCase):
    def setUp(self) -> None:
        with web_api._state_lock:
            web_api._requests_by_ip.clear()
            web_api._public_demo_requests_by_ip.clear()
            web_api._running_tasks.clear()

    def test_public_execution_of_whitelist_task_without_admin_token(self) -> None:
        env = {
            "PUBLIC_DEMO_RUN_ENABLED": "true",
            "PUBLIC_DEMO_TASK_IDS": "MKT-001,MKT-002",
        }
        mock_report = {
            "rules_version": "1.0",
            "pipeline_version": "1.0",
            "records": [{"task_id": "MKT-001", "status": "PASSED"}],
            "summary": {"total": 1},
            "runtime": {"online": True},
            "execution_mode": "live_readonly",
            "writeback": False,
            "public_image_data_url": "data:image/png;base64,ZmFrZQ==",
        }
        with patch.dict(os.environ, env, clear=False), patch("src.web_api.run_public_demo_task", return_value=mock_report) as mock_run:
            handler = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"})
            web_api.handle_demo_run(handler)

            self.assertEqual(handler.status, 200)
            self.assertEqual(handler.json["record"]["task_id"], "MKT-001")
            self.assertEqual((handler.json["execution_mode"], handler.json["writeback"]), ("live_readonly", False))
            self.assertEqual(handler.json["public_image_data_url"], "data:image/png;base64,ZmFrZQ==")
            mock_run.assert_called_once_with("MKT-001")

    def test_public_frontend_shows_readonly_execution_and_inline_image(self) -> None:
        index_html = (Path(__file__).parents[1] / "index.html").read_text(encoding="utf-8")

        self.assertIn("实时读取飞书并重新执行，不会回写数据。", index_html)
        self.assertIn("实时只读运行", index_html)
        self.assertIn("未回写飞书", index_html)
        self.assertIn("DeepSeek 语义复核", index_html)
        self.assertIn("public_image_data_url", index_html)

    def test_public_request_rejects_forbidden_fields(self) -> None:
        env = {"PUBLIC_DEMO_RUN_ENABLED": "true"}
        forbidden_payloads = [
            {"action": "run_task", "task_id": "MKT-001", "source_id": "source_a"},
            {"action": "run_task", "task_id": "MKT-001", "force": True},
            {"action": "run_task", "task_id": "MKT-001", "base_token": "secret_token"},
            {"action": "run_task", "task_id": "MKT-001", "base_url": "https://feishu.cn/base/xxx"},
            {"action": "run_task", "task_id": "MKT-001", "credential_ref": "FEISHU_PRIMARY_APP"},
            {"action": "run_task", "task_id": "MKT-001", "table_id": "tbl123"},
            {"action": "run_task", "task_id": "MKT-001", "record_id": "rec123"},
        ]
        with patch.dict(os.environ, env, clear=False):
            for i, payload in enumerate(forbidden_payloads):
                handler = FakeHandler(payload=payload, ip=f"198.51.100.{10+i}")
                web_api.handle_demo_run(handler)
                self.assertEqual(handler.status, 400, f"Payload {payload} should return 400")
                self.assertIn("非法或敏感字段", handler.json.get("error", ""))

    def test_non_whitelist_task_returns_404(self) -> None:
        env = {
            "PUBLIC_DEMO_RUN_ENABLED": "true",
            "PUBLIC_DEMO_TASK_IDS": "MKT-001,MKT-002",
        }
        with patch.dict(os.environ, env, clear=False):
            handler = FakeHandler(payload={"action": "run_task", "task_id": "MKT-999"})
            web_api.handle_demo_run(handler)
            self.assertEqual(handler.status, 404)
            self.assertIn("未找到指定演示任务", handler.json.get("error", ""))

    def test_public_demo_disabled_returns_503(self) -> None:
        with patch.dict(os.environ, {"PUBLIC_DEMO_RUN_ENABLED": "false"}, clear=False):
            handler = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"})
            web_api.handle_demo_run(handler)
            self.assertEqual(handler.status, 503)

    def test_public_run_next_pending_is_rejected(self) -> None:
        env = {
            "PUBLIC_DEMO_RUN_ENABLED": "true",
            "PUBLIC_DEMO_TASK_IDS": "MKT-001,MKT-002",
        }
        with patch.dict(os.environ, env, clear=False), patch("src.web_api.run_protected_pending") as mock_pending:
            handler = FakeHandler(payload={"action": "run_next_pending"})
            web_api.handle_demo_run(handler)

            self.assertEqual(handler.status, 400)
            self.assertIn("不支持 run_next_pending", handler.json.get("error", ""))
            mock_pending.assert_not_called()

    def test_public_frontend_has_no_pending_task_execution(self) -> None:
        index_html = (Path(__file__).parents[1] / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("演示处理下一条", index_html)
        self.assertNotIn("run_next_pending", index_html)
        self.assertNotIn("public_demo_next_pending", index_html)

    def test_admin_sync_still_requires_token_and_processes_at_most_three_tasks(self) -> None:
        env = {"DEMO_ADMIN_TOKEN": "secret-admin"}
        mock_report = {
            "rules_version": "1.0",
            "pipeline_version": "1.0",
            "records": [{"task_id": "MKT-001"}, {"task_id": "MKT-002"}, {"task_id": "MKT-003"}],
            "summary": {"total": 3},
            "runtime": {"online": True},
            "batch": {"processed": 3},
        }
        with patch.dict(os.environ, env, clear=False), patch("src.web_api.run_protected_pending", return_value=mock_report) as mock_pending:
            unauthorized = FakeHandler(payload={}, token=None, path="/api/sync")
            web_api.handle_sync(unauthorized)
            self.assertEqual(unauthorized.status, 401)

            authorized = FakeHandler(payload={}, token="secret-admin", path="/api/sync")
            web_api.handle_sync(authorized)

        self.assertEqual(authorized.status, 200)
        self.assertEqual(len(authorized.json["records"]), 3)
        mock_pending.assert_called_once_with(limit=3)

    def test_public_rate_limit_exceeded_returns_429(self) -> None:
        env = {
            "PUBLIC_DEMO_RUN_ENABLED": "true",
            "PUBLIC_DEMO_TASK_IDS": "MKT-001",
        }
        mock_report = {
            "rules_version": "1.0",
            "pipeline_version": "1.0",
            "records": [{"task_id": "MKT-001"}],
            "summary": {},
            "runtime": {},
            "execution_mode": "live_readonly",
            "writeback": False,
        }
        with patch.dict(os.environ, env, clear=True), patch("src.web_api.run_public_demo_task", return_value=mock_report):
            for _ in range(8):
                h = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"}, ip="198.51.100.50")
                web_api.handle_demo_run(h)
                self.assertEqual(h.status, 200)

            # 9th request from same IP should be blocked by the default limit (429)
            ninth = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"}, ip="198.51.100.50")
            web_api.handle_demo_run(ninth)
            self.assertEqual(ninth.status, 429)
            self.assertIn("Retry-After", ninth.response_headers)

    def test_public_rate_limit_is_counted_per_ip(self) -> None:
        env = {
            "PUBLIC_DEMO_RUN_ENABLED": "true",
            "PUBLIC_DEMO_TASK_IDS": "MKT-001",
        }
        mock_report = {
            "rules_version": "1.0",
            "pipeline_version": "1.0",
            "records": [{"task_id": "MKT-001"}],
            "summary": {},
            "runtime": {},
            "execution_mode": "live_readonly",
            "writeback": False,
        }
        with patch.dict(os.environ, env, clear=True), patch("src.web_api.run_public_demo_task", return_value=mock_report):
            for _ in range(8):
                handler = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"}, ip="198.51.100.51")
                web_api.handle_demo_run(handler)
                self.assertEqual(handler.status, 200)

            other_ip = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"}, ip="198.51.100.52")
            web_api.handle_demo_run(other_ip)
            self.assertEqual(other_ip.status, 200)

    def test_public_rate_limit_resets_after_window(self) -> None:
        env = {
            "PUBLIC_DEMO_RUN_ENABLED": "true",
            "PUBLIC_DEMO_TASK_IDS": "MKT-001",
            "PUBLIC_DEMO_RATE_LIMIT": "1",
            "PUBLIC_DEMO_RATE_WINDOW_SECONDS": "600",
        }
        mock_report = {
            "rules_version": "1.0",
            "pipeline_version": "1.0",
            "records": [{"task_id": "MKT-001"}],
            "summary": {},
            "runtime": {},
            "execution_mode": "live_readonly",
            "writeback": False,
        }
        with patch.dict(os.environ, env, clear=True), patch("src.web_api.run_public_demo_task", return_value=mock_report), patch("src.web_api.time.monotonic", side_effect=[100.0, 100.0, 700.0]):
            first = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"}, ip="198.51.100.53")
            web_api.handle_demo_run(first)
            self.assertEqual(first.status, 200)

            second = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"}, ip="198.51.100.53")
            web_api.handle_demo_run(second)
            self.assertEqual(second.status, 429)
            self.assertIn("Retry-After", second.response_headers)

            after_window = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"}, ip="198.51.100.53")
            web_api.handle_demo_run(after_window)
            self.assertEqual(after_window.status, 200)

    def test_public_and_admin_rate_limits_are_independent(self) -> None:
        env = {
            "DEMO_ADMIN_TOKEN": "secret-admin",
            "PUBLIC_DEMO_RUN_ENABLED": "true",
            "PUBLIC_DEMO_TASK_IDS": "MKT-001",
            "PUBLIC_DEMO_RATE_LIMIT": "1",
        }
        mock_report = {
            "rules_version": "1.0",
            "pipeline_version": "1.0",
            "records": [{"task_id": "MKT-001"}],
            "summary": {},
            "runtime": {},
            "execution_mode": "live_readonly",
            "writeback": False,
        }
        with patch.dict(os.environ, env, clear=False), \
                patch("src.web_api.run_public_demo_task", return_value=mock_report), \
                patch("src.web_api.run_protected_task", return_value=mock_report):
            # Consume public quota
            h_pub = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"}, ip="198.51.100.60")
            web_api.handle_demo_run(h_pub)
            self.assertEqual(h_pub.status, 200)

            # Public is now limited
            h_pub_2 = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"}, ip="198.51.100.60")
            web_api.handle_demo_run(h_pub_2)
            self.assertEqual(h_pub_2.status, 429)

            # Admin request from same IP should still work and not be blocked by public rate limit
            h_admin = FakeHandler(payload={"task_id": "MKT-001"}, token="secret-admin", ip="198.51.100.60")
            web_api.handle_run(h_admin)
            self.assertEqual(h_admin.status, 200)

    def test_concurrent_execution_of_same_public_task_returns_409(self) -> None:
        env = {
            "PUBLIC_DEMO_RUN_ENABLED": "true",
            "PUBLIC_DEMO_TASK_IDS": "MKT-001",
        }
        with patch.dict(os.environ, env, clear=False):
            with web_api._state_lock:
                web_api._running_tasks.add("public:task:MKT-001")

            handler = FakeHandler(payload={"action": "run_task", "task_id": "MKT-001"})
            web_api.handle_demo_run(handler)
            self.assertEqual(handler.status, 409)
            self.assertIn("请勿重复提交", handler.json.get("error", ""))

    def test_admin_endpoints_still_require_token(self) -> None:
        env = {"DEMO_ADMIN_TOKEN": "secret-admin"}
        with patch.dict(os.environ, env, clear=False):
            for path, handler_fn in [
                ("/api/run", web_api.handle_run),
                ("/api/discover", web_api.handle_discover),
                ("/api/confirm", web_api.handle_confirm),
                ("/api/activate", web_api.handle_activate),
            ]:
                h = FakeHandler(payload={"task_id": "MKT-001"}, token=None, path=path)
                handler_fn(h)
                self.assertEqual(h.status, 401, f"{path} should return 401 without token")

            # GET tasks with source_id without token
            h_tasks = FakeHandler(path="/api/tasks?source_id=source_a", token=None)
            web_api.handle_get(h_tasks, "tasks")
            self.assertEqual(h_tasks.status, 401)


if __name__ == "__main__":
    unittest.main()
