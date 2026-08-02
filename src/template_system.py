"""Small, deterministic template domain used by the marketing renderer.

Templates are deliberately data-only.  They describe four whitelisted dynamic
layer types and normalized rectangles; no executable expression, arbitrary
path, or client supplied renderer option is accepted.
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PROJECT_ROOT / ".agents" / "skills" / "simple-visual-compliance"
BUILTIN_TEMPLATE_DIR = SKILL_ROOT / "assets" / "templates"
DEFAULT_TEMPLATE_ID = "classic-stand"
ALLOWED_RATIOS = {"1:1": (800, 800), "3:4": (600, 800)}
ALLOWED_LAYER_TYPES = {"product_image", "logo", "main_text", "promo_price"}
ALLOWED_FIELDS = {
    "product_image": "product_image_path",
    "logo": "logo_image_path",
    "main_text": "main_text",
    "promo_price": "promo_price",
}
TEMPLATE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SUPPORTED_SCHEMA_VERSION = "1.0"
MAX_BACKGROUND_BYTES = 2 * 1024 * 1024


class TemplateError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.details = code, message, details

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": dict(self.details)}


@dataclass(frozen=True)
class TemplatePersistence:
    path: Path | None
    mode: str
    backend: str = "local_file"


def template_persistence() -> TemplatePersistence:
    """Return an honest capability description; Vercel has no durable adapter."""
    if os.environ.get("VERCEL"):
        return TemplatePersistence(None, "unavailable", "vercel_no_persistent_template_store")
    configured = os.environ.get("TEMPLATE_STORAGE_DIR", "").strip()
    return TemplatePersistence(
        Path(configured) if configured else PROJECT_ROOT / "runtime" / "templates",
        "persistent",
    )


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for candidate in (
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf", "arial.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _template_error(code: str, message: str, **details: Any) -> TemplateError:
    return TemplateError(code, message, **details)


def _rect(layer: Mapping[str, Any]) -> tuple[float, float, float, float]:
    rect = layer.get("rect")
    if not isinstance(rect, Mapping) or set(rect) != {"x", "y", "width", "height"}:
        raise _template_error("TEMPLATE_LAYER_OUT_OF_BOUNDS", "图层必须包含完整的归一化矩形区域")
    try:
        values = tuple(float(rect[key]) for key in ("x", "y", "width", "height"))
    except (TypeError, ValueError) as exc:
        raise _template_error("TEMPLATE_LAYER_OUT_OF_BOUNDS", "图层区域必须为数字") from exc
    x, y, width, height = values
    if not all(math.isfinite(value) for value in values) or x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > 1 or y + height > 1:
        raise _template_error("TEMPLATE_LAYER_OUT_OF_BOUNDS", "图层区域超出画布范围", rect=dict(rect))
    return values


def validate_template(raw: Mapping[str, Any], *, require_published: bool = False) -> dict[str, Any]:
    """Validate a UI-produced template using a strict, intentionally tiny schema."""
    if not isinstance(raw, Mapping):
        raise _template_error("TEMPLATE_INVALID", "模板必须是对象")
    allowed_root = {"schema_version", "template_id", "name", "version", "status", "supported_ratios", "background", "layers"}
    unknown = set(raw) - allowed_root
    if unknown:
        raise _template_error("TEMPLATE_INVALID", "模板包含不支持的字段", fields=sorted(unknown))
    if str(raw.get("schema_version") or "") != SUPPORTED_SCHEMA_VERSION:
        raise _template_error("TEMPLATE_INVALID", "不支持的模板 Schema 版本")
    template_id = str(raw.get("template_id") or "").strip()
    if not TEMPLATE_ID_RE.fullmatch(template_id):
        raise _template_error("TEMPLATE_INVALID", "模板 ID 必须是 2-64 位小写标识", template_id=template_id)
    name = str(raw.get("name") or "").strip()
    if not name or len(name) > 80:
        raise _template_error("TEMPLATE_INVALID", "模板名称不能为空且不得超过 80 字")
    try:
        version = int(raw.get("version"))
    except (TypeError, ValueError) as exc:
        raise _template_error("TEMPLATE_INVALID", "模板版本必须为正整数") from exc
    if version < 1:
        raise _template_error("TEMPLATE_INVALID", "模板版本必须为正整数")
    status = str(raw.get("status") or "").upper()
    if status not in {"DRAFT", "PUBLISHED"} or (require_published and status != "PUBLISHED"):
        raise _template_error("TEMPLATE_INVALID", "模板状态无效")
    ratios = raw.get("supported_ratios")
    if not isinstance(ratios, list) or not ratios or len(set(ratios)) != len(ratios) or any(item not in ALLOWED_RATIOS for item in ratios):
        raise _template_error("TEMPLATE_RATIO_NOT_SUPPORTED", "模板必须声明受支持的画布比例")
    background = raw.get("background")
    if not isinstance(background, Mapping) or set(background) - {"asset", "badge_text"} or not str(background.get("asset") or "").strip():
        raise _template_error("TEMPLATE_ASSET_MISSING", "模板背景图片不存在")
    layers = raw.get("layers")
    if not isinstance(layers, list) or not layers:
        raise _template_error("TEMPLATE_INVALID", "模板必须包含动态区域")
    types: set[str] = set()
    normalized_layers: list[dict[str, Any]] = []
    for layer in layers:
        if not isinstance(layer, Mapping):
            raise _template_error("TEMPLATE_INVALID", "图层必须是对象")
        layer_type = str(layer.get("type") or "")
        if layer_type not in ALLOWED_LAYER_TYPES:
            raise _template_error("TEMPLATE_FIELD_NOT_ALLOWED", "图层类型不受支持", layer_type=layer_type)
        allowed = {"type", "field", "rect", "fit"} if layer_type in {"product_image", "logo"} else {"type", "field", "rect", "max_font_size", "min_font_size", "max_lines", "color", "align"}
        extra = set(layer) - allowed
        if extra:
            raise _template_error("TEMPLATE_INVALID", "图层包含不支持的配置", fields=sorted(extra))
        if str(layer.get("field") or "") != ALLOWED_FIELDS[layer_type]:
            raise _template_error("TEMPLATE_FIELD_NOT_ALLOWED", "图层绑定字段不允许", field=layer.get("field"), layer_type=layer_type)
        _rect(layer)
        if layer_type in {"product_image", "logo"}:
            if layer.get("fit", "contain") not in {"contain", "cover"}:
                raise _template_error("TEMPLATE_INVALID", "图片适配方式仅支持 contain 或 cover")
        else:
            try:
                max_size, min_size, max_lines = int(layer.get("max_font_size")), int(layer.get("min_font_size")), int(layer.get("max_lines"))
            except (TypeError, ValueError) as exc:
                raise _template_error("TEMPLATE_INVALID", "文字区域缺少字号或行数限制") from exc
            if min_size < 8 or max_size < min_size or max_size > 160 or max_lines < 1 or max_lines > 6:
                raise _template_error("TEMPLATE_INVALID", "文字区域排版参数无效")
            color = str(layer.get("color") or "")
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", color) or layer.get("align") not in {"left", "center", "right"}:
                raise _template_error("TEMPLATE_INVALID", "文字颜色或对齐方式无效")
        types.add(layer_type)
        normalized_layers.append(dict(layer))
    required = {"product_image", "main_text", "promo_price"}
    if not required.issubset(types):
        raise _template_error("TEMPLATE_INVALID", "模板必须包含商品图片、主文案和活动价格区域", missing=sorted(required - types))
    return {
        "schema_version": str(raw.get("schema_version") or "1.0"), "template_id": template_id,
        "name": name, "version": version, "status": status,
        "supported_ratios": list(ratios), "background": dict(background), "layers": normalized_layers,
    }


class TemplateRepository:
    def __init__(self, persistence: TemplatePersistence | None = None) -> None:
        self.persistence = persistence or template_persistence()
        self.root = self.persistence.path

    def _builtin_paths(self) -> list[Path]:
        return sorted(BUILTIN_TEMPLATE_DIR.glob("*.json"))

    def _read(self, path: Path, *, builtin: bool) -> dict[str, Any]:
        try:
            item = validate_template(json.loads(path.read_text(encoding="utf-8")), require_published=builtin)
        except (OSError, json.JSONDecodeError) as exc:
            raise _template_error("TEMPLATE_NOT_FOUND", "模板无法读取", template=str(path.name)) from exc
        item["_origin"] = "builtin" if builtin else "runtime"
        item["_root"] = str(path.parent)
        return item

    def _runtime_path(self, template_id: str) -> Path:
        if self.root is None:
            raise _template_error("TEMPLATE_STORAGE_UNAVAILABLE", "当前部署未配置持久化模板存储")
        return self.root / "templates" / f"{template_id}.json"

    def _builtin_by_id(self, template_id: str) -> dict[str, Any] | None:
        for path in self._builtin_paths():
            item = self._read(path, builtin=True)
            if item["template_id"] == template_id:
                return item
        return None

    def get(self, template_id: str, *, allow_draft: bool = False) -> dict[str, Any]:
        builtin = self._builtin_by_id(template_id)
        if builtin:
            return builtin
        if self.root is None:
            raise _template_error("TEMPLATE_NOT_FOUND", "未找到指定模板", template_id=template_id)
        path = self._runtime_path(template_id)
        if not path.exists():
            raise _template_error("TEMPLATE_NOT_FOUND", "未找到指定模板", template_id=template_id)
        item = self._read(path, builtin=False)
        if item["status"] != "PUBLISHED" and not allow_draft:
            raise _template_error("TEMPLATE_NOT_FOUND", "正式任务只能使用已发布模板", template_id=template_id)
        return item

    def resolve(self, template_id: Any, *, allow_draft: bool = False) -> dict[str, Any]:
        return self.get(str(template_id or DEFAULT_TEMPLATE_ID).strip() or DEFAULT_TEMPLATE_ID, allow_draft=allow_draft)

    def list(self, *, include_drafts: bool = False) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for path in self._builtin_paths():
            item = self._read(path, builtin=True)
            result.append({key: value for key, value in item.items() if not key.startswith("_")})
        if self.root and (self.root / "templates").exists():
            for path in sorted((self.root / "templates").glob("*.json")):
                item = self._read(path, builtin=False)
                if include_drafts or item["status"] == "PUBLISHED":
                    result.append({key: value for key, value in item.items() if not key.startswith("_")})
        return result

    def save_draft(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        if self.root is None or self.persistence.mode != "persistent":
            raise _template_error("TEMPLATE_STORAGE_UNAVAILABLE", "当前部署未配置持久化模板存储")
        draft = dict(raw)
        draft["status"] = "DRAFT"
        # Version is server-owned: validate the rest of the document against a
        # neutral value, then derive the persisted revision from existing state.
        draft["version"] = 1
        item = validate_template(draft)
        if self._builtin_by_id(item["template_id"]):
            raise _template_error("TEMPLATE_INVALID", "运行时模板不得覆盖内置模板", template_id=item["template_id"])
        path = self._runtime_path(item["template_id"])
        if path.exists():
            existing = self._read(path, builtin=False)
            item["version"] = existing["version"] + 1 if existing["status"] == "PUBLISHED" else existing["version"]
        else:
            item["version"] = 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        return item

    def publish(self, template_id: str) -> dict[str, Any]:
        if self.root is None or self.persistence.mode != "persistent":
            raise _template_error("TEMPLATE_STORAGE_UNAVAILABLE", "当前部署未配置持久化模板存储")
        if self._builtin_by_id(template_id):
            raise _template_error("TEMPLATE_INVALID", "内置模板已发布且不可覆盖", template_id=template_id)
        path = self._runtime_path(template_id)
        if not path.exists():
            raise _template_error("TEMPLATE_NOT_FOUND", "未找到待发布模板", template_id=template_id)
        item = self._read(path, builtin=False)
        item.pop("_origin", None); item.pop("_root", None)
        item["status"] = "PUBLISHED"
        item = validate_template(item, require_published=True)
        self._background_path(item, self.root)  # verify before the status becomes usable
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
        return item

    def store_background(self, *, filename: str, data_url: str) -> str:
        if self.root is None or self.persistence.mode != "persistent":
            raise _template_error("TEMPLATE_STORAGE_UNAVAILABLE", "当前部署未配置持久化模板存储")
        match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", str(data_url or ""))
        if not match:
            raise _template_error("TEMPLATE_ASSET_MISSING", "背景仅支持 PNG 或 JPG 图片")
        try:
            content = base64.b64decode(match.group(2), validate=True)
        except ValueError as exc:
            raise _template_error("TEMPLATE_ASSET_MISSING", "背景图片编码无效") from exc
        if not content or len(content) > MAX_BACKGROUND_BYTES:
            raise _template_error("TEMPLATE_ASSET_MISSING", "背景图片大小必须在 2MB 以内")
        try:
            with Image.open(__import__("io").BytesIO(content)) as image:
                actual = image.format
                image.verify()
        except Exception as exc:
            raise _template_error("TEMPLATE_ASSET_MISSING", "背景图片无法读取") from exc
        if actual not in {"PNG", "JPEG"}:
            raise _template_error("TEMPLATE_ASSET_MISSING", "背景实际格式必须为 PNG 或 JPG")
        # Never preserve a supplied path; generated names are the only asset locator.
        suffix = ".png" if actual == "PNG" else ".jpg"
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(str(filename or "background")).stem)[:32] or "background"
        relative = f"assets/{safe_label}-{uuid.uuid4().hex}{suffix}"
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return relative

    def _background_path(self, template: Mapping[str, Any], root: Path | None = None) -> Path:
        asset = str((template.get("background") or {}).get("asset") or "")
        # Built-ins are relative to their JSON folder; runtime assets are
        # relative to the storage root so JSON cannot escape that root.
        base = Path(str(template.get("_root") or root or "")) if template.get("_origin") == "builtin" else Path(str(root or self.root or ""))
        path = (base / asset).resolve()
        permitted = (BUILTIN_TEMPLATE_DIR.parent if template.get("_origin") == "builtin" else (root or self.root))
        if not permitted or permitted.resolve() not in path.parents or not path.is_file():
            raise _template_error("TEMPLATE_ASSET_MISSING", "模板背景图片不存在")
        return path

    def background_path(self, template: Mapping[str, Any]) -> Path:
        """Resolve a background only after its template id has been validated."""
        root = BUILTIN_TEMPLATE_DIR if template.get("_origin") == "builtin" else self.root
        return self._background_path(template, root)


def _paste_image(canvas: Image.Image, source: Path, rect: tuple[int, int, int, int], fit: str) -> None:
    if not source.is_file():
        raise _template_error("TEMPLATE_ASSET_MISSING", "图片素材不存在", path=source.name)
    try:
        image = Image.open(source).convert("RGBA")
    except Exception as exc:
        raise _template_error("TEMPLATE_ASSET_MISSING", "图片素材无法读取", path=source.name) from exc
    x, y, width, height = rect
    scale = max(width / image.width, height / image.height) if fit == "cover" else min(width / image.width, height / image.height)
    scaled = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.LANCZOS)
    if fit == "cover":
        left, top = max(0, (scaled.width - width) // 2), max(0, (scaled.height - height) // 2)
        scaled = scaled.crop((left, top, left + width, top + height))
        canvas.paste(scaled, (x, y), scaled)
    else:
        canvas.paste(scaled, (x + (width - scaled.width) // 2, y + (height - scaled.height) // 2), scaled)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        proposed = current + char
        if current and draw.textbbox((0, 0), proposed, font=font)[2] > width:
            lines.append(current); current = char
        else:
            current = proposed
    if current: lines.append(current)
    return lines or [""]


def _draw_text(canvas: Image.Image, layer: Mapping[str, Any], value: str, rect: tuple[int, int, int, int]) -> None:
    x, y, width, height = rect; draw = ImageDraw.Draw(canvas)
    selected: tuple[Any, list[str], int] | None = None
    for size in range(int(layer["max_font_size"]), int(layer["min_font_size"]) - 1, -1):
        font = _font(size, bold=True); lines = _wrap(draw, value, font, width)
        line_height = int(size * 1.25)
        line_widths = [draw.textbbox((0, 0), line, font=font)[2] for line in lines]
        if len(lines) <= int(layer["max_lines"]) and line_height * len(lines) <= height and all(line_width <= width for line_width in line_widths):
            selected = (font, lines, line_height); break
    if selected is None:
        raise _template_error("TEXT_OVERFLOW", "文案无法在最小字号和最大行数内放下")
    font, lines, line_height = selected
    align = layer["align"]; anchor = {"left": "la", "center": "ma", "right": "ra"}[align]
    position_x = x if align == "left" else x + width // 2 if align == "center" else x + width
    for index, line in enumerate(lines):
        draw.text((position_x, y + index * line_height), line, font=font, fill=layer["color"], anchor=anchor)


def render_template(record: Mapping[str, Any], output_dir: Path, *, repository: TemplateRepository | None = None, allow_draft: bool = False) -> dict[str, Any]:
    repo = repository or TemplateRepository()
    template = repo.resolve(record.get("template_id"), allow_draft=allow_draft)
    ratio = str(record.get("aspect_ratio") or "1:1")
    if ratio not in template["supported_ratios"]:
        raise _template_error("TEMPLATE_RATIO_NOT_SUPPORTED", "模板不支持当前画布比例", aspect_ratio=ratio)
    size = ALLOWED_RATIOS.get(ratio)
    if not size:
        raise _template_error("TEMPLATE_RATIO_NOT_SUPPORTED", "不支持的画布比例", aspect_ratio=ratio)
    # Start from RGB so untouched pixels exactly match the background consumers
    # use for comparison; alpha is still preserved on each pasted material.
    # Keep Pillow's historical resize sampling for the built-in background so
    # the default template remains byte-for-byte compatible in visual tests.
    background = Image.open(repo._background_path(template)).convert("RGB").resize(size)
    pristine_background = background.copy()
    for layer in template["layers"]:
        x, y, w, h = _rect(layer); rect = (round(x * size[0]), round(y * size[1]), max(1, round(w * size[0])), max(1, round(h * size[1])))
        kind = layer["type"]
        if kind in {"product_image", "logo"}:
            key = layer["field"]
            path_value = record.get(key)
            if not path_value and template.get("_origin") == "builtin" and record.get("_render_context") != "configured":
                path_value = SKILL_ROOT / "assets" / "images" / ("cha-cup-product-clean-stand.png" if kind == "product_image" else "cha-cup-logo.png")
            if not path_value:
                raise _template_error("TEMPLATE_ASSET_MISSING", "模板需要的图片素材缺失", field=key)
            _paste_image(background, Path(str(path_value)), rect, str(layer.get("fit", "contain")))
        else:
            value = record.get(layer["field"])
            if kind == "promo_price":
                if value in (None, ""):
                    raise _template_error("TEMPLATE_FIELD_NOT_ALLOWED", "活动价格为空")
                number = float(value); value = f"￥{int(number) if number.is_integer() else number:g}"
            _draw_text(background, layer, str(value or ""), rect)
    badge = str((template.get("background") or {}).get("badge_text") or "")
    if badge:
        # Compatibility decoration for the existing built-in background; custom
        # templates cannot create arbitrary extra layer types through the UI.
        from PIL import Image as _I
        search = pristine_background.crop((background.width // 2, 0, background.width, min(120, background.height)))
        mask = _I.new("1", search.size); mask.putdata([r > 200 and g < 100 and b < 100 for r, g, b in search.get_flattened_data()])
        box = mask.getbbox()
        if box:
            left = background.width // 2 + box[0]; top = box[1]; right = background.width // 2 + box[2]; bottom = box[3]
            font = _font(10, bold=True)
            for candidate_size in range(18, 9, -1):
                candidate = _font(candidate_size, bold=True)
                text_box = ImageDraw.Draw(pristine_background).textbbox((0, 0), badge, font=candidate)
                if text_box[2] - text_box[0] <= max(1, right - left - 20):
                    font = candidate; break
            # Render into a clipped overlay: font metrics can overhang slightly,
            # but no rendered badge pixel may escape the red background bounds.
            overlay = Image.new("RGBA", background.size, (0, 0, 0, 0))
            ImageDraw.Draw(overlay).text(((left + right) // 2, (top + bottom) // 2), badge, fill="#ffffff", font=font, anchor="mm")
            clipped = overlay.crop((left, top, right, bottom))
            background.paste(clipped, (left, top), clipped)
    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", str(record.get("task_id") or "TASK-000")).strip("_") or "TASK-000"
    output_dir.mkdir(parents=True, exist_ok=True); filename = f"{safe_id}_rendered.png"
    background.save(output_dir / filename, "PNG", optimize=True)
    return {"filename": filename, "width": size[0], "height": size[1], "format": "PNG", "template": {key: template[key] for key in ("template_id", "name", "version", "status")}}
