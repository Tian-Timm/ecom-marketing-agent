#!/usr/bin/env python3
"""运行唯一的营销任务审计与图片组装流水线。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
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
from assemble_image import assemble_batch

SCHEMA_VERSION = "1.0"

def build_report(
    records: List[Dict[str, Any]],
    source: str,
    rules: Dict[str, Any],
) -> Dict[str, Any]:
    passed = sum(1 for record in records if record["status"] == "PASSED")
    blocked = len(records) - passed
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules_version": str(rules.get("version", "unknown")),
        "source": source,
        "summary": {
            "total": len(records),
            "passed": passed,
            "blocked": blocked,
            "images_generated": passed,
        },
        "records": records,
    }

def run_records(
    raw_records: List[Dict[str, Any]],
    output_dir: Path,
    source: str = "runtime",
) -> Dict[str, Any]:
    standardized = [sanitize_record(record, index) for index, record in enumerate(raw_records)]
    rules = load_rules()
    audited = audit_batch(standardized, rules=rules)
    assembled = assemble_batch(audited, output_dir)
    return build_report(assembled, source=source, rules=rules)

def run_pipeline(input_path: Path, output_dir: Path) -> Dict[str, Any]:
    standardized = process_file(input_path)
    rules = load_rules()
    audited = audit_batch(standardized, rules=rules)
    assembled = assemble_batch(audited, output_dir)
    return build_report(assembled, source=input_path.name, rules=rules)

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
