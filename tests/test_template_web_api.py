import io
import json
import os
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from src.web_api import handle_template_background, handle_template_save, handle_template_upload, handle_templates


class Handler:
    def __init__(self, payload=None, token=None, path="/"):
        encoded = json.dumps(payload or {}).encode("utf-8")
        self.rfile = io.BytesIO(encoded)
        self.wfile = io.BytesIO()
        self.headers = {"Content-Length": str(len(encoded))}
        if token is not None:
            self.headers["X-Demo-Admin-Token"] = token
        self.client_address = ("127.0.0.1", 0)
        self.path = path
        self.status = None

    def send_response(self, status): self.status = int(status)
    def send_header(self, *_): pass
    def end_headers(self): pass


def payload():
    return {"template": {"schema_version":"1.0", "template_id":"api-template", "name":"接口模板", "version":1, "status":"DRAFT", "supported_ratios":["1:1"], "background":{"asset":"assets/background.png"}, "layers":[
        {"type":"product_image","field":"product_image_path","rect":{"x":.1,"y":.1,"width":.2,"height":.2},"fit":"contain"},
        {"type":"main_text","field":"main_text","rect":{"x":.1,"y":.4,"width":.7,"height":.2},"max_font_size":24,"min_font_size":12,"max_lines":2,"color":"#FFFFFF","align":"center"},
        {"type":"promo_price","field":"promo_price","rect":{"x":.1,"y":.7,"width":.2,"height":.1},"max_font_size":24,"min_font_size":12,"max_lines":1,"color":"#000000","align":"left"}
    ]}}


class TemplateWebApiTests(unittest.TestCase):
    def test_template_management_reads_and_writes_require_admin_token(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEMO_ADMIN_TOKEN":"correct", "TEMPLATE_STORAGE_DIR":directory}, clear=False):
            without = Handler(payload())
            handle_template_save(without)
            self.assertEqual(without.status, 401)
            wrong = Handler(payload(), "wrong")
            handle_template_save(wrong)
            self.assertEqual(wrong.status, 401)
            correct = Handler(payload(), "correct")
            handle_template_save(correct)
            self.assertEqual(correct.status, 200)
            listed = Handler(token="correct")
            handle_templates(listed)
            self.assertEqual(listed.status, 200)
            response = json.loads(listed.wfile.getvalue())
            self.assertIn("api-template", {item["template_id"] for item in response["templates"]})

    def test_background_upload_has_its_own_bounded_limit_above_normal_json_limit(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"DEMO_ADMIN_TOKEN":"correct", "TEMPLATE_STORAGE_DIR":directory}, clear=False):
            image = io.BytesIO()
            Image.effect_noise((180, 180), 90).convert("RGB").save(image, "PNG")
            body = {"filename":"background.png", "data_url":"data:image/png;base64," + base64.b64encode(image.getvalue()).decode("ascii")}
            self.assertGreater(len(json.dumps(body).encode("utf-8")), 8 * 1024)
            handler = Handler(body, "correct")
            handle_template_upload(handler)
            self.assertEqual(handler.status, 200)
            self.assertTrue(json.loads(handler.wfile.getvalue())["asset"].startswith("assets/"))

    def test_background_read_requires_token_and_accepts_only_template_id(self):
        with patch.dict(os.environ, {"DEMO_ADMIN_TOKEN":"correct"}, clear=False):
            denied = Handler(path="/api/template_background?template_id=classic-stand")
            handle_template_background(denied)
            self.assertEqual(denied.status, 401)
            allowed = Handler(token="correct", path="/api/template_background?template_id=classic-stand")
            handle_template_background(allowed)
            self.assertEqual(allowed.status, 200)
            self.assertGreater(len(allowed.wfile.getvalue()), 100)
            traversal = Handler(token="correct", path="/api/template_background?template_id=../../secret")
            handle_template_background(traversal)
            self.assertNotEqual(traversal.status, 200)


if __name__ == "__main__":
    unittest.main()
