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

def require_assets() -> None:
    missing = [
        str(path)
        for path in (PRODUCT_IMG_PATH, LOGO_IMG_PATH, STAND_BG_PATH)
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

    require_assets()
    width, height = ALLOWED_RATIOS[aspect_ratio]
    canvas = Image.open(STAND_BG_PATH).convert("RGBA").resize((width, height))

    logo = Image.open(LOGO_IMG_PATH).convert("RGBA")
    logo_w = 120 if width == 600 else 140
    logo_h = int(logo.height * (logo_w / logo.width))
    logo = logo.resize((logo_w, logo_h), Image.Resampling.LANCZOS)
    canvas.paste(logo, (24, 24), logo)

    prod = Image.open(PRODUCT_IMG_PATH).convert("RGBA")
    prod_h = 400 if width == 600 else 440
    prod_w = int(prod.width * (prod_h / prod.height))
    prod = prod.resize((prod_w, prod_h), Image.Resampling.LANCZOS)
    prod_x = (width - prod_w) // 2
    canvas.paste(prod, (prod_x, 160), prod)

    draw = ImageDraw.Draw(canvas)

    promo_price = rec.get("promo_price")
    price_num = float(promo_price)
    price_val_str = str(int(price_num)) if price_num.is_integer() else f"{price_num:g}"
    
    font_price_label = get_font(18 if width == 600 else 20)
    font_price_num = get_font(36 if width == 600 else 42, bold=True)
    
    y_start = height - 160
    draw.text((24, y_start + 20), "到手价", fill=(140, 77, 0), font=font_price_label)
    draw.text((24, y_start + 55), f"￥{price_val_str}", fill=(0, 0, 0), font=font_price_num)

    main_text = str(rec.get("main_text") or "").strip()
    if main_text:
        copy_left = 220 if width == 600 else 310
        copy_width = width - copy_left - 24
        font_main_text = fit_font(
            draw,
            main_text,
            max_width=copy_width,
            start_size=28 if width == 600 else 36,
        )
        x_center = copy_left + copy_width // 2
        draw.text((x_center, y_start + 80), main_text, fill=(255, 255, 255), font=font_main_text, anchor="mm")

    badge_text = "爆款推荐"
    badge_left, badge_top, badge_right, badge_bottom = find_red_badge_bounds(canvas)
    badge_padding = 10
    font_badge = fit_font(
        draw,
        badge_text,
        max_width=badge_right - badge_left - badge_padding * 2,
        start_size=18 if width == 600 else 22,
        min_size=14,
    )
    badge_center = (
        (badge_left + badge_right) // 2,
        (badge_top + badge_bottom) // 2,
    )
    draw.text(
        badge_center,
        badge_text,
        fill=(255, 255, 255),
        font=font_badge,
        anchor="mm",
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    task_id = safe_task_id(rec.get("task_id"))
    filename = f"{task_id}_rendered.png"
    out_file = output_dir / filename
    canvas.convert("RGB").save(out_file, "PNG", optimize=True)
    return {
        "filename": filename,
        "width": width,
        "height": height,
        "format": "PNG",
    }

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
