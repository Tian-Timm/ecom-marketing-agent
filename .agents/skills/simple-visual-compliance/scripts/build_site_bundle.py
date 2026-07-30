#!/usr/bin/env python3
"""将静态演示打包成 Sites 可部署的 Cloudflare Worker。"""

from __future__ import annotations

import base64
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
OUTPUT_DIR = PROJECT_ROOT / "generated_output"
SOCIAL_PREVIEW = (
    PROJECT_ROOT
    / ".agents"
    / "skills"
    / "simple-visual-compliance"
    / "assets"
    / "images"
    / "social-preview.png"
)
DIST_SERVER = PROJECT_ROOT / "dist" / "server"
WORKER_PATH = DIST_SERVER / "index.js"


def encode_file(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def build_worker() -> Path:
    index_path = PROJECT_ROOT / "index.html"
    report_path = OUTPUT_DIR / "pipeline_result.json"
    image_paths = sorted(OUTPUT_DIR.glob("*_rendered.png"))
    required = [index_path, report_path, SOCIAL_PREVIEW, *image_paths]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"部署缺少必要文件: {', '.join(missing)}")
    if not image_paths:
        raise ValueError("部署前至少需要一张真实流水线图片产物")

    files: dict[str, dict[str, str]] = {
        "/generated_output/pipeline_result.json": {
            "contentType": "application/json; charset=utf-8",
            "base64": encode_file(report_path),
            "cacheControl": "no-store",
        },
        "/og.png": {
            "contentType": "image/png",
            "base64": encode_file(SOCIAL_PREVIEW),
            "cacheControl": "public, max-age=86400",
        },
    }
    for image_path in image_paths:
        files[f"/generated_output/{image_path.name}"] = {
            "contentType": "image/png",
            "base64": encode_file(image_path),
            "cacheControl": "public, max-age=86400",
        }

    index_b64 = encode_file(index_path)
    files_json = json.dumps(files, ensure_ascii=False, separators=(",", ":"))
    worker_source = f"""const INDEX_HTML_B64 = {json.dumps(index_b64)};
const FILES = {files_json};

function decodeBase64(value) {{
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}}

function responseHeaders(contentType, cacheControl) {{
  return {{
    "Content-Type": contentType,
    "Cache-Control": cacheControl,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "SAMEORIGIN"
  }};
}}

export default {{
  async fetch(request) {{
    const url = new URL(request.url);
    const path = url.pathname === "/" ? "/index.html" : url.pathname;
    if (path === "/index.html") {{
      const source = new TextDecoder().decode(decodeBase64(INDEX_HTML_B64));
      const socialMeta = [
        '<meta property="og:type" content="website">',
        '<meta property="og:title" content="CHA CUP 营销图片工作台">',
        '<meta property="og:description" content="先审计，再出图。体验业务规则阻断与确定性图片组装。">',
        `<meta property="og:image" content="${{url.origin}}/og.png">`,
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:title" content="CHA CUP 营销图片工作台">',
        '<meta name="twitter:description" content="先审计，再出图。体验业务规则阻断与确定性图片组装。">',
        `<meta name="twitter:image" content="${{url.origin}}/og.png">`
      ].join("");
      const html = source.replace("</head>", `${{socialMeta}}</head>`);
      return new Response(request.method === "HEAD" ? null : html, {{
        status: 200,
        headers: responseHeaders("text/html; charset=utf-8", "no-store")
      }});
    }}

    const file = FILES[path];
    if (file) {{
      return new Response(
        request.method === "HEAD" ? null : decodeBase64(file.base64),
        {{
          status: 200,
          headers: responseHeaders(file.contentType, file.cacheControl)
        }}
      );
    }}

    return new Response("Not found", {{
      status: 404,
      headers: responseHeaders("text/plain; charset=utf-8", "no-store")
    }});
  }}
}};
"""
    DIST_SERVER.mkdir(parents=True, exist_ok=True)
    WORKER_PATH.write_text(worker_source, encoding="utf-8")
    return WORKER_PATH


if __name__ == "__main__":
    output = build_worker()
    print(f"Sites 部署包已生成: {output}")
