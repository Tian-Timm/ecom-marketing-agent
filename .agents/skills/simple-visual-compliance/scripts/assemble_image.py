#!/usr/bin/env python3
"""只负责确定性图层组装，不参与业务合规判定。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image, ImageDraw, ImageFont

SKILL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_DIR.parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src.template_system import TemplateRepository, render_template
MOCK_ASSETS_DIR = SKILL_DIR / "assets" / "images"
BACKGROUNDS_DIR = SKILL_DIR / "assets" / "backgrounds"

PRODUCT_IMG_PATH = MOCK_ASSETS_DIR / "cha-cup-product-clean-stand.png"
if not PRODUCT_IMG_PATH.exists():
    PRODUCT_IMG_PATH = MOCK_ASSETS_DIR / "cha-cup-product-transparent.png"

LOGO_IMG_PATH = MOCK_ASSETS_DIR / "cha-cup-logo.png"
STAND_BG_PATH = BACKGROUNDS_DIR / "stand_3d_bg.png"
ALLOWED_RATIOS = {"1:1": (800, 800), "3:4": (600, 800)}

def get_font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    font_names = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "arial.ttf",
    ]
    for font_name in font_names:
        try:
            return ImageFont.truetype(font_name, size)
        except OSError:
            continue
    return ImageFont.load_default()

def safe_task_id(value: Any) -> str:
    task_id = str(value or "TASK-000").strip()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", task_id).strip("_")
    return safe or "TASK-000"

def require_assets(product_path: Path = PRODUCT_IMG_PATH, logo_path: Path = LOGO_IMG_PATH) -> None:
    missing = [
        str(path)
        for path in (product_path, logo_path, STAND_BG_PATH)
        if not path.exists()
    ]
    if missing:
        raise FileNotFoundError(f"缺少组装素材: {', '.join(missing)}")

def fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    start_size: int,
    min_size: int = 18,
) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for size in range(start_size, min_size - 1, -2):
        font = get_font(size, bold=True)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= max_width:
            return font
    return get_font(min_size, bold=True)

def find_red_badge_bounds(image: Image.Image) -> tuple[int, int, int, int]:
    """从模板顶部右侧识别红色标签，避免画幅缩放后继续使用固定坐标。"""
    search_left = image.width // 2
    search_bottom = min(120, image.height)
    search_area = image.convert("RGB").crop(
        (search_left, 0, image.width, search_bottom)
    )
    red_pixels = Image.new("1", search_area.size)
    red_pixels.putdata([
        red > 200 and green < 100 and blue < 100
        for red, green, blue in search_area.get_flattened_data()
    ])
    bounds = red_pixels.getbbox()
    if bounds is None:
        raise ValueError("背景模板中未找到顶部红色标签")
    return (
        search_left + bounds[0],
        bounds[1],
        search_left + bounds[2],
        bounds[3],
    )

def assemble_single_image(rec: Dict[str, Any], output_dir: Path) -> Dict[str, Any]:
    if rec.get("status") != "PASSED":
        raise ValueError("只有 PASSED 任务可以进入图片组装")

    aspect_ratio = str(rec.get("aspect_ratio") or "1:1").strip()
    if aspect_ratio not in ALLOWED_RATIOS:
        raise ValueError(f"不支持的画布比例: {aspect_ratio}")
    if rec.get("promo_price") in (None, ""):
        raise ValueError("组装图片前必须提供活动价")

    # Layout selection is deterministic and comes solely from the validated
    # internal template.  Keep this function as the legacy pipeline seam.
    return render_template(rec, output_dir, repository=TemplateRepository())

def assemble_batch(records: List[Dict[str, Any]], output_dir: Path) -> List[Dict[str, Any]]:
    updated: List[Dict[str, Any]] = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for rec in records:
        r = dict(rec)
        task_id = safe_task_id(r.get("task_id"))
        expected_path = output_dir / f"{task_id}_rendered.png"
        if r.get("status") == "PASSED":
            artifact = assemble_single_image(r, output_dir)
            r["generated_image"] = artifact["filename"]
            r["artifact"] = artifact
        else:
            if expected_path.exists():
                expected_path.unlink()
            r["generated_image"] = None
            r["artifact"] = None
        r.pop("generated_image_path", None)
        updated.append(r)
    return updated

def main() -> None:
    output_dir = PROJECT_ROOT / "generated_output"
    if len(sys.argv) > 1:
        in_path = Path(sys.argv[1])
        records = json.loads(in_path.read_text(encoding="utf-8"))
    else:
        records = json.load(sys.stdin)

    results = assemble_batch(records, output_dir)
    print(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
