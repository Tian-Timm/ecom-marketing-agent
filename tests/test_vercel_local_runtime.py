import importlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class VercelLocalRuntimeContractTests(unittest.TestCase):
    def test_python_functions_avoid_local_builder_max_duration_metadata(self) -> None:
        """Vercel CLI 58 local Python builds reject builder maxDuration output."""
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))

        for pattern, options in config.get("functions", {}).items():
            if pattern.endswith(".py"):
                self.assertNotIn(
                    "maxDuration",
                    options,
                    "vercel dev currently returns HTTP 500 before Python starts when "
                    "a Python function build contains maxDuration metadata",
                )

    def test_python_api_entries_bootstrap_project_imports_without_loading_dotenv(self) -> None:
        for path in sorted((ROOT / "api").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            with self.subTest(api=path.name):
                bootstrap = source.find("sys.path.insert(0, str(root))")
                src_import = source.find("from src.web_api")
                self.assertGreaterEqual(bootstrap, 0)
                self.assertGreater(src_import, bootstrap)
                self.assertNotIn("local_environment", source)

    def test_template_urls_share_one_python_function_with_explicit_rewrites(self) -> None:
        """Hobby deployments keep all public template URLs behind one Function."""
        api_entries = sorted((ROOT / "api").glob("*.py"))
        self.assertLessEqual(len(api_entries), 12)
        self.assertTrue((ROOT / "api" / "template.py").is_file())
        for legacy_name in (
            "templates.py",
            "template_background.py",
            "template_upload.py",
            "template_save.py",
            "template_test.py",
            "template_publish.py",
        ):
            self.assertFalse((ROOT / "api" / legacy_name).exists())

        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        rewrites = {item["source"]: item["destination"] for item in config["rewrites"]}
        self.assertEqual(
            {path: rewrites.get(path) for path in (
                "/api/templates",
                "/api/template_background",
                "/api/template_upload",
                "/api/template_save",
                "/api/template_test",
                "/api/template_publish",
            )},
            {
                "/api/templates": "/api/template?action=templates",
                "/api/template_background": "/api/template?action=background",
                "/api/template_upload": "/api/template?action=upload",
                "/api/template_save": "/api/template?action=save",
                "/api/template_test": "/api/template?action=test",
                "/api/template_publish": "/api/template?action=publish",
            },
        )

    def test_unified_template_handler_preserves_query_and_enforces_methods(self) -> None:
        module = importlib.import_module("api.template")

        class Request:
            def __init__(self, path: str, method: str, token: str | None = None) -> None:
                self.path = path
                self.command = method
                self.rfile = io.BytesIO(b"{}")
                self.wfile = io.BytesIO()
                self.headers = {"Content-Length": "2"}
                if token:
                    self.headers["X-Demo-Admin-Token"] = token
                self.client_address = ("127.0.0.1", 0)
                self.status = None

            def send_response(self, status: int) -> None:
                self.status = int(status)

            def send_header(self, *_: object) -> None:
                pass

            def end_headers(self) -> None:
                pass

        denied_list = Request("/api/template?action=templates", "GET")
        module.handler.do_GET(denied_list)
        self.assertEqual(denied_list.status, 401)
        self.assertIn("error", json.loads(denied_list.wfile.getvalue()))

        denied_post = Request("/api/template?action=save", "POST")
        module.handler.do_POST(denied_post)
        self.assertEqual(denied_post.status, 401)
        self.assertIn("error", json.loads(denied_post.wfile.getvalue()))

        wrong_method = Request("/api/template?action=save", "GET")
        module.handler.do_GET(wrong_method)
        self.assertEqual(wrong_method.status, 405)
        self.assertEqual(json.loads(wrong_method.wfile.getvalue())["error"], "method_not_allowed")

        unsupported_verb = Request("/api/template?action=templates", "PUT")
        module.handler.do_PUT(unsupported_verb)
        self.assertEqual(unsupported_verb.status, 405)
        self.assertEqual(json.loads(unsupported_verb.wfile.getvalue())["error"], "method_not_allowed")

        with patch.dict(os.environ, {"DEMO_ADMIN_TOKEN": "correct"}, clear=False):
            background = Request(
                "/api/template?action=background&template_id=classic-stand",
                "GET",
                token="correct",
            )
            module.handler.do_GET(background)
        self.assertEqual(background.status, 200)
        self.assertGreater(len(background.wfile.getvalue()), 100)


if __name__ == "__main__":
    unittest.main()
