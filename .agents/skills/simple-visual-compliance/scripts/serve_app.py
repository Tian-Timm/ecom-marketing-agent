#!/usr/bin/env python3
"""提供本地真实执行接口，并托管根目录前端。"""

from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = SKILL_DIR.parents[2]
OUTPUT_DIR = PROJECT_ROOT / "generated_output"
REPORT_PATH = OUTPUT_DIR / "pipeline_result.json"
sys.path.insert(0, str(SCRIPT_DIR))

from audit_text import load_rules
from run_pipeline import build_report, run_records, write_report

MAX_BODY_BYTES = 64 * 1024

class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(PROJECT_ROOT), **kwargs)

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/api/status":
            rules = load_rules()
            self.send_json({
                "online": True,
                "mode": "local",
                "rules_version": str(rules.get("version", "unknown")),
            })
            return
        if self.path == "/api/tasks":
            if not REPORT_PATH.exists():
                self.send_json({"error": "尚未生成流水线报告"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(json.loads(REPORT_PATH.read_text(encoding="utf-8")))
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/api/run":
            self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("请求内容为空或超过 64KB")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求内容必须是一个任务对象")
            report = run_records([payload], OUTPUT_DIR, source="local-api")
            record = report["records"][0]
            existing_records = []
            if REPORT_PATH.exists():
                existing_report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
                if isinstance(existing_report, dict):
                    existing_records = list(existing_report.get("records", []))
            record_index = next(
                (
                    index
                    for index, item in enumerate(existing_records)
                    if item.get("task_id") == record.get("task_id")
                ),
                None,
            )
            if record_index is None:
                existing_records.insert(0, record)
            else:
                existing_records[record_index] = record
            persisted = build_report(
                existing_records,
                source="local-api",
                rules=load_rules(),
            )
            write_report(persisted, OUTPUT_DIR)
            self.send_json({
                "rules_version": report["rules_version"],
                "record": record,
            })
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": f"执行失败: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[本地服务] {self.address_string()} {format % args}")

def main() -> None:
    host = "127.0.0.1"
    port = 8765
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f"CHA CUP 本地工作台已启动: http://{host}:{port}")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
