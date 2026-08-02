import base64
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / ".agents" / "skills" / "simple-visual-compliance" / "assets" / "templates" / "schema" / "template.schema.json"
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "dataset-standardizer" / "scripts"))

from standardize import sanitize_record
from src.template_system import (
    DEFAULT_TEMPLATE_ID, TemplateError, TemplatePersistence, TemplateRepository,
    render_template, template_persistence, validate_template,
)


def template(template_id="campaign-template", status="DRAFT", background="assets/bg.png"):
    return {
        "schema_version": "1.0", "template_id": template_id, "name": "测试模板", "version": 1,
        "status": status, "supported_ratios": ["1:1"], "background": {"asset": background},
        "layers": [
            {"type":"product_image","field":"product_image_path","rect":{"x":.1,"y":.1,"width":.3,"height":.3},"fit":"contain"},
            {"type":"main_text","field":"main_text","rect":{"x":.1,"y":.5,"width":.7,"height":.2},"max_font_size":28,"min_font_size":12,"max_lines":2,"color":"#FFFFFF","align":"center"},
            {"type":"promo_price","field":"promo_price","rect":{"x":.1,"y":.75,"width":.3,"height":.1},"max_font_size":28,"min_font_size":12,"max_lines":1,"color":"#000000","align":"left"},
        ],
    }


class TemplateSystemTests(unittest.TestCase):
    def test_standardizer_maps_template_alias_and_preserves_value(self):
        self.assertEqual(sanitize_record({"模板": "summer-sale"}, 0)["template_id"], "summer-sale")
        self.assertEqual(sanitize_record({"主文案": "杯子"}, 0)["template_id"], "")

    def test_builtin_default_renders_and_reports_template(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = render_template({"task_id":"DEFAULT", "aspect_ratio":"1:1", "main_text":"城市轻装", "promo_price":99}, Path(directory))
            self.assertTrue((Path(directory) / artifact["filename"]).exists())
            self.assertEqual(artifact["template"]["template_id"], DEFAULT_TEMPLATE_ID)

    def test_configured_builtin_run_never_falls_back_to_demo_materials(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TemplateError) as captured:
                render_template({"task_id":"FORMAL", "aspect_ratio":"1:1", "main_text":"城市轻装", "promo_price":99, "_render_context":"configured"}, Path(directory))
            self.assertEqual(captured.exception.code, "TEMPLATE_ASSET_MISSING")

    def test_draft_cannot_be_resolved_for_formal_task_but_can_for_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = TemplateRepository(TemplatePersistence(root, "persistent"))
            bg = root / "assets" / "bg.png"; bg.parent.mkdir(); Image.new("RGB", (50, 50), "white").save(bg)
            repo.save_draft(template())
            with self.assertRaises(TemplateError) as captured:
                repo.resolve("campaign-template")
            self.assertEqual(captured.exception.code, "TEMPLATE_NOT_FOUND")
            self.assertEqual(repo.resolve("campaign-template", allow_draft=True)["status"], "DRAFT")
            self.assertEqual(repo.publish("campaign-template")["status"], "PUBLISHED")

    def test_reediting_a_published_runtime_template_increments_version_server_side(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = TemplateRepository(TemplatePersistence(root, "persistent"))
            bg = root / "assets" / "bg.png"; bg.parent.mkdir(); Image.new("RGB", (50, 50), "white").save(bg)
            initial = template(); initial["version"] = 999
            self.assertEqual(repo.save_draft(initial)["version"], 1)
            # Existing DRAFT preserves the server revision despite a client jump.
            self.assertEqual(repo.save_draft(initial)["version"], 1)
            self.assertEqual(repo.publish("campaign-template")["version"], 1)
            edited = template(); edited["name"] = "已编辑"; edited["version"] = 999
            self.assertEqual(repo.save_draft(edited)["version"], 2)
            self.assertEqual(repo.publish("campaign-template")["version"], 2)
            # Editing a published v2 correctly advances to v3, not client 999.
            edited["version"] = 999
            self.assertEqual(repo.save_draft(edited)["version"], 3)

    def test_schema_describes_background_and_each_whitelisted_layer(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertTrue(schema["properties"]["supported_ratios"]["uniqueItems"])
        background = schema["properties"]["background"]
        self.assertEqual(background["required"], ["asset"])
        self.assertFalse(background["additionalProperties"])
        variants = schema["properties"]["layers"]["items"]["oneOf"]
        self.assertEqual(len(variants), 4)
        self.assertEqual(variants[0]["properties"]["fit"]["enum"], ["contain", "cover"])
        rect = schema["$defs"]["rect"]
        self.assertFalse(rect["additionalProperties"])
        self.assertEqual(set(rect["required"]), {"x", "y", "width", "height"})
        self.assertEqual(schema["$defs"]["text_layer"]["properties"]["max_font_size"]["maximum"], 160)

    def test_validation_rejects_out_of_bounds_and_illegal_binding(self):
        invalid = template(); invalid["layers"][0]["rect"]["width"] = 1.1
        with self.assertRaises(TemplateError) as captured:
            validate_template(invalid)
        self.assertEqual(captured.exception.code, "TEMPLATE_LAYER_OUT_OF_BOUNDS")
        invalid = template(); invalid["layers"][0]["field"] = "../../secret"
        with self.assertRaises(TemplateError) as captured:
            validate_template(invalid)
        self.assertEqual(captured.exception.code, "TEMPLATE_FIELD_NOT_ALLOWED")
        invalid = template(); invalid["schema_version"] = "99.0"
        with self.assertRaises(TemplateError) as captured:
            validate_template(invalid)
        self.assertEqual(captured.exception.code, "TEMPLATE_INVALID")
        for value in (float("nan"), float("inf")):
            invalid = template(); invalid["layers"][0]["rect"]["x"] = value
            with self.assertRaises(TemplateError) as captured:
                validate_template(invalid)
            self.assertEqual(captured.exception.code, "TEMPLATE_LAYER_OUT_OF_BOUNDS")
        invalid = template(); invalid["layers"][1]["max_font_size"] = 161
        with self.assertRaises(TemplateError) as captured:
            validate_template(invalid)
        self.assertEqual(captured.exception.code, "TEMPLATE_INVALID")

    def test_ratio_and_text_overflow_are_structured(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = TemplateRepository(TemplatePersistence(root, "persistent"))
            bg = root / "assets" / "bg.png"; bg.parent.mkdir(); Image.new("RGB", (50, 50), "white").save(bg)
            item = template(status="PUBLISHED"); repo.save_draft(item); repo.publish(item["template_id"])
            product = root / "product.png"; Image.new("RGBA", (20, 20), "blue").save(product)
            with self.assertRaises(TemplateError) as captured:
                render_template({"task_id":"RATIO", "template_id":item["template_id"], "aspect_ratio":"3:4", "main_text":"x", "promo_price":1, "product_image_path":product}, root, repository=repo)
            self.assertEqual(captured.exception.code, "TEMPLATE_RATIO_NOT_SUPPORTED")
            with self.assertRaises(TemplateError) as captured:
                render_template({"task_id":"TEXT", "template_id":item["template_id"], "aspect_ratio":"1:1", "main_text":"过" * 500, "promo_price":1, "product_image_path":product}, root, repository=repo)
            self.assertEqual(captured.exception.code, "TEXT_OVERFLOW")

    def test_single_character_that_exceeds_narrow_text_box_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = TemplateRepository(TemplatePersistence(root, "persistent"))
            bg = root / "assets" / "bg.png"; bg.parent.mkdir(); Image.new("RGB", (50, 50), "white").save(bg)
            product = root / "product.png"; Image.new("RGBA", (20, 20), "blue").save(product)
            item = template(template_id="narrow-text", status="PUBLISHED")
            item["layers"][1]["rect"] = {"x": .1, "y": .5, "width": .001, "height": .2}
            repo.save_draft(item); repo.publish(item["template_id"])
            with self.assertRaises(TemplateError) as captured:
                render_template({"task_id":"NARROW", "template_id":item["template_id"], "aspect_ratio":"1:1", "main_text":"宽", "promo_price":1, "product_image_path":product}, root, repository=repo)
            self.assertEqual(captured.exception.code, "TEXT_OVERFLOW")

    def test_image_contain_and_cover_have_predictable_region_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = TemplateRepository(TemplatePersistence(root, "persistent"))
            bg = root / "assets" / "bg.png"; bg.parent.mkdir(); Image.new("RGB", (50, 50), "white").save(bg)
            product = root / "product.png"; Image.new("RGBA", (100, 20), "blue").save(product)
            for fit, expected_corner in (("contain", (255, 255, 255)), ("cover", (0, 0, 255))):
                item = template(template_id=f"fit-{fit}", status="PUBLISHED")
                item["layers"][0]["fit"] = fit
                repo.save_draft(item); repo.publish(item["template_id"])
                artifact = render_template({"task_id":fit, "template_id":item["template_id"], "aspect_ratio":"1:1", "main_text":"x", "promo_price":1, "product_image_path":product}, root, repository=repo)
                rendered = Image.open(root / artifact["filename"]).convert("RGB")
                self.assertEqual(rendered.getpixel((90, 90)), expected_corner)

    def test_runtime_template_reports_missing_declared_image_material(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); repo = TemplateRepository(TemplatePersistence(root, "persistent"))
            bg = root / "assets" / "bg.png"; bg.parent.mkdir(); Image.new("RGB", (50, 50), "white").save(bg)
            item = template(template_id="needs-product", status="PUBLISHED")
            repo.save_draft(item); repo.publish(item["template_id"])
            with self.assertRaises(TemplateError) as captured:
                render_template({"task_id":"missing", "template_id":item["template_id"], "aspect_ratio":"1:1", "main_text":"x", "promo_price":1}, root, repository=repo)
            self.assertEqual(captured.exception.code, "TEMPLATE_ASSET_MISSING")

    def test_upload_validates_actual_image_format_and_never_uses_client_path(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = TemplateRepository(TemplatePersistence(Path(directory), "persistent"))
            image = io.BytesIO(); Image.new("RGB", (20, 20), "white").save(image, "PNG")
            data = "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()
            stored = repo.store_background(filename="../../unsafe.png", data_url=data)
            self.assertTrue(stored.startswith("assets/unsafe-"))
            self.assertNotIn("..", stored)
            with self.assertRaises(TemplateError):
                repo.store_background(filename="bad.jpg", data_url="data:image/jpeg;base64," + base64.b64encode(b"not an image").decode())

    def test_vercel_storage_is_not_advertised_as_persistent(self):
        previous = os.environ.get("VERCEL")
        try:
            os.environ["VERCEL"] = "1"
            self.assertEqual(template_persistence().mode, "unavailable")
        finally:
            if previous is None: os.environ.pop("VERCEL", None)
            else: os.environ["VERCEL"] = previous


if __name__ == "__main__":
    unittest.main()
