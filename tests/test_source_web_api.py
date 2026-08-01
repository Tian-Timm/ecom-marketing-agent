from __future__ import annotations

import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src import web_api
from src.business_semantics import SourceConfigRevisionConflictError
from src.source_composition import ConfigPersistence, config_persistence


class FakeHandler:
    """Small in-process Vercel handler fixture; it never opens a network socket."""

    def __init__(self, *, payload: dict | None = None, token: str = "admin", path: str = "/") -> None:
        body = json.dumps(payload or {}).encode("utf-8")
        self.headers = {
            "Content-Length": str(len(body)),
            "X-Demo-Admin-Token": token,
            "X-Forwarded-For": "198.51.100.8",
        }
        self.rfile, self.wfile = io.BytesIO(body), io.BytesIO()
        self.client_address = ("198.51.100.8", 443)
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


def persistent(repo) -> tuple[object, object]:
    return repo, SimpleNamespace(mode="persistent")


def source_config(source_id: str = "source_a"):
    field = lambda field_id: SimpleNamespace(field_id=field_id)
    return SimpleNamespace(
        source_id=source_id,
        display_name="安全数据源",
        revision=2,
        connector=SimpleNamespace(base_token=f"base-{source_id}-private", credential_ref="FEISHU_PRIMARY_APP"),
        tables={
            "tasks": SimpleNamespace(table_id=f"tasks-{source_id}", fields={
                "task_id": field("task"), "product_id": field("product"), "img_type": field("type"),
                "aspect_ratio": field("ratio"), "main_text": field("main"), "promo_price": field("price"),
                "deploy_date": field("date"),
            }),
            "products": SimpleNamespace(table_id=f"products-{source_id}", fields={
                "product_id": field("product"), "product_name": field("name"), "min_price": field("min"),
            }),
        },
        writeback={
            "status": field("status"), "issues": field("issues"), "processed_at": field("processed"),
            "input_hash": field("hash"), "pipeline_version": field("pipeline"), "image_attachment": field("image"),
        },
    )


def valid_selection() -> dict:
    return {
        "product_table_id": "products-source_a", "task_table_id": "tasks-source_a",
        "writeback_table_id": "tasks-source_a",
        "product_fields": {"product_id": "product", "product_name": "name", "min_price": "min"},
        "task_fields": {"task_id": "task", "product_id": "product", "img_type": "type", "aspect_ratio": "ratio", "main_text": "main", "promo_price": "price", "deploy_date": "date"},
        "writeback_fields": {"status": "status", "issues": "issues", "processed_at": "processed", "input_hash": "hash", "pipeline_version": "pipeline", "image_attachment": "image"},
        "product_reference_strategy": "AUTO",
    }


class SourceWebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        with web_api._state_lock:
            web_api._requests_by_ip.clear()
            web_api._running_tasks.clear()
        self.environment = patch.dict(os.environ, {"DEMO_ADMIN_TOKEN": "admin", "FEISHU_APP_SECRET": "never-return-this"}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _confirm_payload(self, **overrides) -> dict:
        payload = {"source_id": "source_a", "display_name": "A", "base_url": "base-source_a-private", "credential_ref": "FEISHU_PRIMARY_APP", "revision": 2, "schema_fingerprint": "forged-by-client", "selection": valid_selection()}
        payload.update(overrides)
        return payload

    def test_discover_confirm_activate_uses_server_snapshot_and_sanitizes_responses(self) -> None:
        repo = MagicMock()
        snapshot = SimpleNamespace(source_id="source_a", display_name="A", schema_fingerprint="server-schema-v2", tables=())
        result = SimpleNamespace(config=SimpleNamespace(revision=2, status=SimpleNamespace(value="DRAFT")), dry_run=SimpleNamespace(passed=True, issues=()))
        with patch.object(web_api, "resolve_feishu_credential", return_value=object()), patch.object(web_api, "discover_source", return_value=snapshot) as discover, patch("src.business_semantics.mapping_candidates", return_value=[]), patch.object(web_api, "config_repository", return_value=persistent(repo)), patch.object(web_api, "confirm_draft_config", return_value=result) as confirm:
            discover_handler = FakeHandler(payload={"source_id": "source_a", "display_name": "A", "base_url": "base-source_a-private", "credential_ref": "FEISHU_PRIMARY_APP"})
            web_api.handle_discover(discover_handler)
            self.assertEqual((discover_handler.status, discover_handler.json["schema_fingerprint"]), (200, "server-schema-v2"))
            confirm_handler = FakeHandler(payload=self._confirm_payload())
            web_api.handle_confirm(confirm_handler)
            self.assertEqual(confirm_handler.status, 200)
            self.assertIs(confirm.call_args.args[1], snapshot)
            self.assertNotEqual(confirm.call_args.args[1].schema_fingerprint, confirm_handler.headers.get("schema_fingerprint", "forged-by-client"))
            activate_handler = FakeHandler(payload={"source_id": "source_a", "revision": 2, "expected_active_revision": 1})
            web_api.handle_activate(activate_handler)
        self.assertEqual(activate_handler.status, 200)
        repo.activate.assert_called_once_with("source_a", 2, expected_active_revision=1)
        for handler in (discover_handler, confirm_handler, activate_handler):
            encoded = json.dumps(handler.json)
            self.assertNotIn("never-return-this", encoded)
            self.assertNotIn("FEISHU_APP_SECRET", encoded)
            self.assertNotIn("base-source_a-private", encoded)

    def test_configured_get_post_auth_and_quota_are_separate(self) -> None:
        unauthorized_get = FakeHandler(token="bad", path="/api/tasks?source_id=source_a")
        web_api.handle_get(unauthorized_get, "tasks")
        unauthorized_post = FakeHandler(token="bad", payload={})
        web_api.handle_discover(unauthorized_post)
        self.assertEqual((unauthorized_get.status, unauthorized_post.status), (401, 401))
        repo = MagicMock()
        snapshot = SimpleNamespace(source_id="source_a", display_name="A", schema_fingerprint="server", tables=())
        adapter = MagicMock()
        adapter.list_fields.side_effect = lambda _base, table_id: [
            {"field_id": field_id, "field_name": name}
            for field_id, name in ({
                "task": "任务", "product": "商品", "type": "类型", "ratio": "比例", "main": "主文案", "price": "活动价", "date": "投放日期",
                "status": "状态", "issues": "问题", "processed": "处理时间", "hash": "指纹", "pipeline": "版本", "image": "图片",
            } if table_id.startswith("tasks-") else {"product": "商品", "name": "商品名", "min": "底价"}).items()
        ]
        adapter.list_records.return_value = []
        with patch.object(web_api, "_source_context", return_value=(repo, SimpleNamespace(mode="persistent"), source_config(), adapter)):
            admin_get = FakeHandler(path="/api/tasks?source_id=source_a")
            web_api.handle_get(admin_get, "tasks")
        self.assertEqual(admin_get.status, 200)
        self.assertEqual(web_api._requests_by_ip, {})
        with patch.object(web_api, "resolve_feishu_credential", return_value=object()), patch.object(web_api, "discover_source", return_value=snapshot), patch("src.business_semantics.mapping_candidates", return_value=[]):
            admin_post = FakeHandler(payload={"source_id": "source_a", "display_name": "A", "base_url": "base", "credential_ref": "FEISHU_PRIMARY_APP"})
            web_api.handle_discover(admin_post)
        self.assertEqual(admin_post.status, 200)
        self.assertEqual(len(web_api._requests_by_ip["198.51.100.8"]), 1)

    def test_sources_and_confirm_responses_never_expose_connector_details(self) -> None:
        repo = MagicMock()
        repo.list_source_ids.return_value = ["source_a"]
        repo.get_active.return_value = source_config()
        with patch.object(web_api, "config_repository", return_value=persistent(repo)):
            handler = FakeHandler(path="/api/sources")
            web_api.handle_get(handler, "sources")
        self.assertEqual(handler.status, 200)
        encoded = json.dumps(handler.json)
        self.assertNotIn("base-source_a-private", encoded)
        self.assertNotIn("credential_ref", encoded)
        self.assertNotIn("FEISHU_APP_SECRET", encoded)

    def test_configured_task_summary_normalizes_single_value_cells(self) -> None:
        config, repo, adapter = source_config(), MagicMock(), MagicMock()
        task_names = {"task": "任务", "product": "商品", "type": "类型", "ratio": "比例", "main": "主文案", "price": "活动价", "date": "投放日期", "status": "状态", "issues": "问题", "processed": "处理时间", "hash": "指纹", "pipeline": "版本", "image": "图片"}
        product_names = {"product": "商品", "name": "商品名", "min": "底价"}
        adapter.list_fields.side_effect = lambda _base, table_id: [{"field_id": key, "field_name": value} for key, value in (task_names if table_id.startswith("tasks-") else product_names).items()]
        adapter.list_records.side_effect = lambda _base, table_id: (
            [{"商品": ["P-1"], "商品名": [{"text": "CHA CUP"}], "底价": [89]}]
            if table_id.startswith("products-") else
            [{"任务": [{"text": "TASK-1"}], "商品": ["P-1"], "类型": ["电商主图"], "比例": ["1:1"], "主文案": [{"text": "轻装出发"}], "活动价": [99], "投放日期": ["2026-08-01"], "状态": ["审查通过"]}]
        )
        with patch.object(web_api, "_source_context", return_value=(repo, SimpleNamespace(mode="persistent"), config, adapter)):
            handler = FakeHandler(path="/api/tasks?source_id=source_a")
            web_api.handle_get(handler, "tasks")
        record = handler.json["records"][0]
        self.assertEqual((handler.status, record["status"], record["task_id"], record["product_id"], record["product_name"]), (200, "PASSED", "TASK-1", "P-1", "CHA CUP"))

    def test_ephemeral_confirmation_and_activation_are_rejected(self) -> None:
        repo = MagicMock()
        ephemeral = (repo, SimpleNamespace(mode="ephemeral"))
        with patch.object(web_api, "config_repository", return_value=ephemeral):
            confirm_handler = FakeHandler(payload=self._confirm_payload())
            activate_handler = FakeHandler(payload={"source_id": "source_a", "revision": 2})
            web_api.handle_confirm(confirm_handler)
            web_api.handle_activate(activate_handler)
        self.assertEqual((confirm_handler.status, activate_handler.status), (503, 503))

    def test_vercel_ignores_directory_override_and_disables_dynamic_onboarding(self) -> None:
        with patch.dict(os.environ, {"VERCEL": "1", "SOURCE_CONFIG_DIR": "D:/pretend-persistent"}, clear=False):
            persistence = config_persistence()
        self.assertEqual((persistence.mode, persistence.backend), ("ephemeral", "vercel_ephemeral_local_file"))
        self.assertEqual(persistence.path.as_posix(), "/tmp/ecom-source-config")
        with patch.object(web_api, "runtime_status", return_value={"online": True, "capabilities": {}}), patch.object(web_api, "config_repository", return_value=(MagicMock(), ConfigPersistence(None, "ephemeral", "vercel_ephemeral_local_file"))):
            status = FakeHandler(path="/api/status")
            web_api.handle_get(status, "status")
            confirm = FakeHandler(payload=self._confirm_payload())
            web_api.handle_confirm(confirm)
        self.assertFalse(status.json["capabilities"]["onboarding_write"])
        self.assertEqual(confirm.status, 503)

    def test_source_run_forwards_identity_and_releases_only_its_own_lock(self) -> None:
        repo, adapter = MagicMock(), MagicMock()
        orchestrator = MagicMock()
        orchestrator.run_task.return_value = {"task_id": "TASK-1", "source_id": "source_a", "status": "PASSED", "image_file_token": "token-a"}
        with patch.object(web_api, "_source_context", return_value=(repo, SimpleNamespace(mode="persistent"), source_config(), adapter)), patch.object(web_api, "ConfiguredSourceOrchestrator", return_value=orchestrator):
            handler = FakeHandler(payload={"source_id": "source_a", "task_id": "TASK-1"})
            web_api.handle_run(handler)
        self.assertEqual(handler.status, 200)
        orchestrator.run_task.assert_called_once_with("source_a", "TASK-1", dry_run=False, force=False)
        self.assertNotIn("source_a:TASK-1", web_api._running_tasks)
        with web_api._state_lock:
            web_api._running_tasks.add("source_a:TASK-1")
        duplicate = FakeHandler(payload={"source_id": "source_a", "task_id": "TASK-1"})
        web_api.handle_run(duplicate)
        self.assertEqual(duplicate.status, 409)
        self.assertIn("source_a:TASK-1", web_api._running_tasks)

    def test_source_image_token_cannot_cross_source_boundary(self) -> None:
        config_a, config_b = source_config("source_a"), source_config("source_b")
        adapter_a, adapter_b = MagicMock(), MagicMock()
        for adapter, allowed in ((adapter_a, "token-a"), (adapter_b, "token-b")):
            adapter.list_fields.return_value = [{"field_id": "image", "field_name": "生成图片"}]
            adapter.list_records.return_value = [{"生成图片": [{"file_token": allowed}]}]
        def context(source_id: str):
            return MagicMock(), SimpleNamespace(mode="persistent"), config_a if source_id == "source_a" else config_b, adapter_a if source_id == "source_a" else adapter_b
        with patch.object(web_api, "_source_context", side_effect=context):
            handler = FakeHandler(path="/api/image?source_id=source_b&file_token=token-a")
            web_api.handle_image(handler)
        self.assertEqual(handler.status, 403)
        adapter_b.download_media.assert_not_called()

    def test_malformed_selection_is_400_and_revision_conflict_is_409(self) -> None:
        repo = MagicMock()
        with patch.object(web_api, "config_repository", return_value=persistent(repo)):
            malformed = FakeHandler(payload=self._confirm_payload(selection={"task_fields": []}))
            web_api.handle_confirm(malformed)
        self.assertEqual(malformed.status, 400)
        snapshot = SimpleNamespace(source_id="source_a", display_name="A", schema_fingerprint="server", tables=())
        with patch.object(web_api, "config_repository", return_value=persistent(repo)), patch.object(web_api, "resolve_feishu_credential", return_value=object()), patch.object(web_api, "discover_source", return_value=snapshot), patch.object(web_api, "confirm_draft_config", side_effect=SourceConfigRevisionConflictError("source_a", "版本冲突")):
            conflict = FakeHandler(payload=self._confirm_payload())
            web_api.handle_confirm(conflict)
        self.assertEqual(conflict.status, 409)


if __name__ == "__main__":
    unittest.main()
