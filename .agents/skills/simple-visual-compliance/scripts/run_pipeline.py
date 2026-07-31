#!/usr/bin/env python3
"""运行唯一的营销任务审计与图片组装流水线。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List

SKILL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_DIR.parents[2]
STANDARDIZE_SCRIPT = PROJECT_ROOT / ".agents" / "skills" / "dataset-standardizer" / "scripts" / "standardize.py"
AUDIT_SCRIPT = SKILL_DIR / "scripts" / "audit_text.py"
ASSEMBLE_SCRIPT = SKILL_DIR / "scripts" / "assemble_image.py"

sys.path.insert(0, str(STANDARDIZE_SCRIPT.parent))
sys.path.insert(0, str(AUDIT_SCRIPT.parent))

from standardize import process_file, sanitize_record
from audit_text import audit_batch, load_rules
from assemble_image import assemble_batch, safe_task_id

SCHEMA_VERSION = "2.0"
PIPELINE_VERSION = "2026-07-31.1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _input_hash(record: Dict[str, Any]) -> str:
    payload = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _trace_step(
    name: str,
    label: str,
    status: str,
    started_at: str,
    started_counter: float,
    detail: str,
) -> Dict[str, Any]:
    return {
        "name": name,
        "label": label,
        "status": status,
        "started_at": started_at,
        "duration_ms": round((perf_counter() - started_counter) * 1000, 2),
        "detail": detail,
    }


def _remove_stale_image(task_id: Any, output_dir: Path) -> None:
    stale_image = output_dir / f"{safe_task_id(task_id)}_rendered.png"
    if stale_image.exists():
        stale_image.unlink()


def execute_task(
    raw_record: Dict[str, Any],
    output_dir: Path,
    *,
    index: int = 0,
    rules: Dict[str, Any] | None = None,
    source: str = "runtime",
) -> Dict[str, Any]:
    """统一执行一个任务，并返回可供命令行、网页和飞书复用的结果。"""
    task_started_at = _utc_now()
    task_started_counter = perf_counter()
    trace: List[Dict[str, Any]] = []
    rules = rules or load_rules()
    record: Dict[str, Any] = dict(raw_record)

    try:
        step_started_at = _utc_now()
        step_counter = perf_counter()
        record = sanitize_record(raw_record, index)
        input_hash = _input_hash(record)
        trace.append(_trace_step(
            "normalize",
            "信息整理",
            "COMPLETED",
            step_started_at,
            step_counter,
            "已整理并识别任务信息。",
        ))

        step_started_at = _utc_now()
        step_counter = perf_counter()
        record = audit_batch([record], rules=rules)[0]
        audit_status = "COMPLETED" if record["status"] == "PASSED" else "BLOCKED"
        audit_detail = (
            "价格、日期、画布比例和文案检查通过。"
            if record["status"] == "PASSED"
            else f"发现 {len(record.get('violations', []))} 项需要修改的内容。"
        )
        trace.append(_trace_step(
            "deterministic_audit",
            "规则审查",
            audit_status,
            step_started_at,
            step_counter,
            audit_detail,
        ))

        step_started_at = _utc_now()
        step_counter = perf_counter()
        trace.append(_trace_step(
            "semantic_review",
            "语义复核",
            "NOT_CONFIGURED",
            step_started_at,
            step_counter,
            "语义复核暂未启用。",
        ))

        step_started_at = _utc_now()
        step_counter = perf_counter()
        if record["status"] == "PASSED":
            record = assemble_batch([record], output_dir)[0]
            render_status = "COMPLETED"
            render_detail = "营销图片已生成。"
        else:
            record = assemble_batch([record], output_dir)[0]
            render_status = "SKIPPED"
            render_detail = "请先处理审查问题，再生成营销图片。"
        trace.append(_trace_step(
            "render",
            "图片生成",
            render_status,
            step_started_at,
            step_counter,
            render_detail,
        ))

        step_started_at = _utc_now()
        step_counter = perf_counter()
        trace.append(_trace_step(
            "delivery",
            "飞书交付",
            "NOT_CONFIGURED",
            step_started_at,
            step_counter,
            "飞书交付暂未启用。",
        ))
        record["input_hash"] = input_hash
        record["pipeline_version"] = PIPELINE_VERSION
        record["source"] = source
        record["started_at"] = task_started_at
        record["duration_ms"] = round((perf_counter() - task_started_counter) * 1000, 2)
        record["execution_trace"] = trace
        return record
    except Exception as exc:
        _remove_stale_image(record.get("task_id"), output_dir)
        trace.append({
            "name": "internal_error",
            "label": "任务执行",
            "status": "FAILED",
            "started_at": _utc_now(),
            "duration_ms": 0,
            "detail": str(exc),
        })
        return {
            **record,
            "status": "FAILED",
            "blocked_reason": "",
            "violations": [],
            "rules_version": str(rules.get("version", "unknown")),
            "generated_image": None,
            "artifact": None,
            "input_hash": _input_hash(record),
            "pipeline_version": PIPELINE_VERSION,
            "source": source,
            "started_at": task_started_at,
            "duration_ms": round((perf_counter() - task_started_counter) * 1000, 2),
            "execution_trace": trace,
            "error": {
                "code": "PIPELINE_EXECUTION_FAILED",
                "message": str(exc),
            },
        }

def build_report(
    records: List[Dict[str, Any]],
    source: str,
    rules: Dict[str, Any],
) -> Dict[str, Any]:
    passed = sum(1 for record in records if record["status"] == "PASSED")
    blocked = sum(1 for record in records if record["status"] == "BLOCKED")
    review_required = sum(
        1 for record in records if record["status"] == "REVIEW_REQUIRED"
    )
    failed = sum(1 for record in records if record["status"] == "FAILED")
    return {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules_version": str(rules.get("version", "unknown")),
        "source": source,
        "summary": {
            "total": len(records),
            "passed": passed,
            "blocked": blocked,
            "review_required": review_required,
            "failed": failed,
            "images_generated": passed,
        },
        "records": records,
    }

def run_records(
    raw_records: List[Dict[str, Any]],
    output_dir: Path,
    source: str = "runtime",
) -> Dict[str, Any]:
    rules = load_rules()
    records = [
        execute_task(
            record,
            output_dir,
            index=index,
            rules=rules,
            source=source,
        )
        for index, record in enumerate(raw_records)
    ]
    return build_report(records, source=source, rules=rules)

def run_pipeline(input_path: Path, output_dir: Path) -> Dict[str, Any]:
    standardized = process_file(input_path)
    return run_records(standardized, output_dir, source=input_path.name)

def write_report(report: Dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "pipeline_result.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_path

def main() -> None:
    parser = argparse.ArgumentParser(description="运行营销合规审计与确定性图片组装")
    parser.add_argument("--input", "-i", required=True, help="CSV 或 JSON 输入路径")
    parser.add_argument("--output-dir", "-o", default=str(PROJECT_ROOT / "generated_output"), help="图片和报告输出目录")
    args = parser.parse_args()

    input_file = Path(args.input)
    output_dir = Path(args.output_dir)

    if not input_file.exists():
        print(f"输入文件不存在: {input_file}", file=sys.stderr)
        sys.exit(1)

    report = run_pipeline(input_file, output_dir)
    report_path = write_report(report, output_dir)
    summary = report["summary"]
    print("流水线执行完成")
    print(f"任务总数: {summary['total']}")
    print(f"通过: {summary['passed']}")
    print(f"阻断: {summary['blocked']}")
    print(f"报告: {report_path}")

if __name__ == "__main__":
    main()
