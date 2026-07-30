"""CHA CUP 营销图片设计系统 - 配置文件与全局常数"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径配置
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output_images"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ENV_FILE = BASE_DIR / ".env"
ASSETS_DIR = BASE_DIR / ".agents" / "skills" / "fde-mock-marketing-assets" / "assets"
PRODUCT_IMAGE_PATH = ASSETS_DIR / "images" / "cha-cup-product.png"
LOGO_IMAGE_PATH = ASSETS_DIR / "images" / "cha-cup-logo.png"

# ---------------------------------------------------------------------------
# 环境初始化 (.env 读取)
# ---------------------------------------------------------------------------

def load_environment() -> None:
    if not ENV_FILE.exists():
        return
    raw = ENV_FILE.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-16")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())

load_environment()

# ---------------------------------------------------------------------------
# 飞书与业务规则常数
# ---------------------------------------------------------------------------

FEISHU_BASE_TOKEN = os.environ.get("FEISHU_BASE_TOKEN", "")
PRODUCT_TABLE_NAME = "商品资料"
TASK_TABLE_NAME = "出图任务"

LARK_CLI_CMD = shutil.which("lark-cli") or "lark-cli"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# VI 与风控规则参数
MIN_ALLOW_PROMO_PRICE = 89.0
FORBIDDEN_WORDS = ["全网第一", "绝对保温", "永不漏水", "100%不漏"]
ALLOWED_ASPECT_RATIOS = {"1:1": (2000, 2000), "3:4": (1500, 2000)}
LOGO_COLOR_HEX = "#244638"
LOGO_WIDTH_RATIO_MIN = 0.12
LOGO_WIDTH_RATIO_MAX = 0.20
LOGO_SAFE_MARGIN_RATIO = 0.50

# 默认中文字体路径 (Windows)
FONT_PATH = "C:/Windows/Fonts/msyh.ttc"
