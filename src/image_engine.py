"""M3 图像合成与视觉排版引擎 - 严格遵循 VI 规范高保真渲染"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .config import (
    ALLOWED_ASPECT_RATIOS,
    FONT_PATH,
    LOGO_COLOR_HEX,
    LOGO_IMAGE_PATH,
    LOGO_SAFE_MARGIN_RATIO,
    LOGO_WIDTH_RATIO_MIN,
    OUTPUT_DIR,
    PRODUCT_IMAGE_PATH,
)


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """加载中文字体，自动退避至默认字体。"""
    try:
        font_file = "C:/Windows/Fonts/msyhbd.ttc" if bold else FONT_PATH
        if not Path(font_file).exists():
            font_file = FONT_PATH
        return ImageFont.truetype(font_file, size=size)
    except Exception:
        return ImageFont.load_default()


def render_marketing_image(task: dict[str, Any], product: dict[str, Any]) -> Path:
    """按 VI 规范及任务参数渲染电商主图/促销海报，返回输出文件路径。"""
    task_id = str(task.get("任务ID") or "MKT-UNKNOWN")
    ratio = str(task.get("画布比例") or "1:1")
    canvas_size = ALLOWED_ASPECT_RATIOS.get(ratio, (2000, 2000))
    width, height = canvas_size

    # 1. 创建画布 (白底 / 促销底色)
    canvas = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # 2. 绘制微淡优雅修饰背景 (如果是 3:4 海报，增加底部大促色彩带)
    if ratio == "3:4":
        # 底部淡绿背景区块 (#F2F7F4)
        bottom_box_height = int(height * 0.28)
        draw.rectangle(
            [(0, height - bottom_box_height), (width, height)],
            fill=(242, 247, 244, 255),
        )
        # 顶部促销细长带
        draw.rectangle([(0, 0), (width, int(height * 0.04))], fill=(36, 70, 56, 255))

    # 3. 加载并合成白底产品母图 (居中定位)
    if PRODUCT_IMAGE_PATH.exists():
        product_img = Image.open(PRODUCT_IMAGE_PATH).convert("RGBA")
        p_w, p_h = product_img.size

        # 调整产品缩放比例 (占画布高度 50%-60%)
        target_p_h = int(height * 0.52)
        scale_ratio = target_p_h / p_h
        target_p_w = int(p_w * scale_ratio)
        product_scaled = product_img.resize((target_p_w, target_p_h), Image.Resampling.LANCZOS)

        # 放置位置 (垂直居中微靠下)
        paste_x = (width - target_p_w) // 2
        paste_y = int(height * 0.22) if ratio == "1:1" else int(height * 0.18)
        canvas.paste(product_scaled, (paste_x, paste_y), product_scaled)

    # 4. 按 VI 规范加载并放置透明 Logo
    if LOGO_IMAGE_PATH.exists():
        logo_img = Image.open(LOGO_IMAGE_PATH).convert("RGBA")
        l_w, l_h = logo_img.size

        # VI 规范: 宽度占画布 15% (满足 12%-20%)
        target_logo_w = int(width * 0.16)
        logo_scale = target_logo_w / l_w
        target_logo_h = int(l_h * logo_scale)
        logo_scaled = logo_img.resize((target_logo_w, target_logo_h), Image.Resampling.LANCZOS)

        # VI 规范: 安全区四周空白 >= 50% Logo 高度
        safe_margin = int(target_logo_h * LOGO_SAFE_MARGIN_RATIO)
        logo_x = max(safe_margin, int(width * 0.05))
        logo_y = max(safe_margin, int(height * 0.04))
        canvas.paste(logo_scaled, (logo_x, logo_y), logo_scaled)

    # 5. 绘制营销主文案与活动价格
    main_copy = str(task.get("主文案") or "城市轻装 随时出发")
    activity_name = str(task.get("活动名称") or "限时特惠")
    price_val = task.get("活动价")
    price_str = f"￥{int(price_val)}" if isinstance(price_val, (int, float)) else f"￥{product.get('日常价', 129)}"

    if ratio == "1:1":
        # === 1:1 电商主图排版 ===
        # 顶部活动名横幅
        banner_font = get_font(int(width * 0.024), bold=True)
        title_font = get_font(int(width * 0.042), bold=True)
        price_font = get_font(int(width * 0.058), bold=True)
        sub_font = get_font(int(width * 0.022))

        # 底部价格卡片胶囊 (#244638 深森林绿)
        card_w, card_h = int(width * 0.88), int(height * 0.15)
        card_x = (width - card_w) // 2
        card_y = height - card_h - int(height * 0.05)

        draw.rounded_rectangle(
            [(card_x, card_y), (card_x + card_w, card_y + card_h)],
            radius=int(card_h * 0.25),
            fill=(36, 70, 56, 255),
        )

        # 胶囊内文案
        draw.text(
            (card_x + int(card_w * 0.06), card_y + int(card_h * 0.22)),
            main_copy,
            font=title_font,
            fill=(255, 255, 255, 255),
        )
        draw.text(
            (card_x + int(card_w * 0.06), card_y + int(card_h * 0.65)),
            f"容量 500ml | {activity_name}",
            font=sub_font,
            fill=(200, 225, 210, 255),
        )

        # 价格高亮红圈/气泡
        price_bg_w = int(card_w * 0.32)
        price_bg_x = card_x + card_w - price_bg_w - int(card_w * 0.04)
        price_bg_y = card_y + int(card_h * 0.15)
        draw.rounded_rectangle(
            [(price_bg_x, price_bg_y), (price_bg_x + price_bg_w, card_y + card_h - int(card_h * 0.15))],
            radius=int(card_h * 0.18),
            fill=(230, 57, 70, 255),
        )
        draw.text(
            (price_bg_x + int(price_bg_w * 0.15), price_bg_y + int(card_h * 0.08)),
            price_str,
            font=price_font,
            fill=(255, 255, 255, 255),
        )

    else:
        # === 3:4 促销海报排版 ===
        title_font = get_font(int(width * 0.048), bold=True)
        sub_font = get_font(int(width * 0.026))
        price_font = get_font(int(width * 0.065), bold=True)
        label_font = get_font(int(width * 0.024), bold=True)

        # 底部布局
        b_y = int(height * 0.76)
        draw.text((int(width * 0.08), b_y), main_copy, font=title_font, fill=(36, 70, 56, 255))
        draw.text(
            (int(width * 0.08), b_y + int(height * 0.05)),
            f"轻量杯身 · 旋拧杯盖 · 6小时保温 | {activity_name}",
            font=sub_font,
            fill=(100, 120, 110, 255),
        )

        # 促销价格板块
        price_x = int(width * 0.68)
        price_y = b_y - int(height * 0.02)
        draw.rounded_rectangle(
            [(price_x, price_y), (price_x + int(width * 0.25), price_y + int(height * 0.12))],
            radius=20,
            fill=(230, 57, 70, 255),
        )
        draw.text((price_x + 15, price_y + 10), "活动价", font=label_font, fill=(255, 255, 255, 220))
        draw.text((price_x + 15, price_y + 40), price_str, font=price_font, fill=(255, 255, 255, 255))

    # 6. 保存导出 PNG
    output_path = OUTPUT_DIR / f"{task_id}_rendered.png"
    final_canvas = canvas.convert("RGB")
    final_canvas.save(output_path, "PNG", quality=95)
    return output_path
