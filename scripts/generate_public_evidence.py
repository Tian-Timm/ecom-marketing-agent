#!/usr/bin/env python3
"""Regenerate the public pipeline snapshot and sanitized evidence summary."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PROJECT_ROOT / ".agents" / "skills" / "simple-visual-compliance"
SCRIPTS_DIR = SKILL_DIR / "scripts"
OUTPUT_DIR = PROJECT_ROOT / "generated_output"
INPUT_PATH = SKILL_DIR / "assets" / "marketing_tasks.csv"
EVALUATION_PATH = OUTPUT_DIR / "evaluation_result.json"
PIPELINE_PATH = OUTPUT_DIR / "pipeline_result.json"
PUBLIC_EVIDENCE_PATH = OUTPUT_DIR / "public_evidence.json"

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(PROJECT_ROOT / ".agents" / "skills" / "dataset-standardizer" / "scripts"))

from audit_text import load_rules  # noqa: E402
from run_pipeline import build_report, execute_task, write_report  # noqa: E402
from standardize import process_file  # noqa: E402


TEST_COMMANDS = (
    (
        "root",
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
    ),
    (
        "simple_visual_compliance",
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            ".agents/skills/simple-visual-compliance/tests",
            "-p",
            "test_*.py",
        ],
    ),
)


def _run_test_suite(name: str, command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    match = re.search(r"Ran\s+(\d+)\s+tests?", output)
    if not match:
        raise RuntimeError(f"无法读取 {name} 测试数量：{output[-1000:]}")
    total = int(match.group(1))
    if completed.returncode != 0:
        raise RuntimeError(f"{name} 测试未通过：{output[-2000:]}")
    return {"passed": total, "total": total}


def _run_static_snapshot() -> dict[str, Any]:
    """Run deterministic fixture rules without making an online semantic call."""
    output_dir = OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    rules = load_rules()
    records = [
        execute_task(
            record,
            output_dir,
            index=index,
            rules=rules,
            source="static_snapshot",
            semantic_reviewer=None,
        )
        for index, record in enumerate(process_file(INPUT_PATH))
    ]
    report = build_report(records, source=f"static_fixture:{INPUT_PATH.name}", rules=rules)
    report["execution_mode"] = "static_snapshot"
    report["writeback"] = False
    for record in report["records"]:
        record["source"] = "static_snapshot"
        for step in record.get("execution_trace", []):
            if step.get("name") == "semantic_review":
                step["status"] = "SKIPPED"
                step["detail"] = "静态快照不执行在线 DeepSeek 语义复核；公开实时运行会重新执行。"
            elif step.get("name") == "delivery":
                step["status"] = "SKIPPED"
                step["detail"] = "静态快照不访问飞书交付；本快照不会上传或回写。"
    report["feishu_sync"] = {
        "mode": "static_snapshot",
        "records_read": len(report["records"]),
        "records_written": 0,
        "images_uploaded": 0,
        "unchanged_skipped": 0,
    }
    write_report(report, output_dir)
    return report


def _run_evaluation() -> dict[str, Any]:
    evaluator = SCRIPTS_DIR / "evaluate_fixture.py"
    evaluation_env = os.environ.copy()
    evaluation_env.pop("DEEPSEEK_API_KEY", None)
    completed = subprocess.run(
        [sys.executable, str(evaluator)],
        cwd=PROJECT_ROOT,
        env=evaluation_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"固定评测失败：{completed.stdout}\n{completed.stderr}")
    return json.loads(EVALUATION_PATH.read_text(encoding="utf-8"))


def _git_commit(explicit: str | None) -> str:
    if explicit:
        return explicit
    completed = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def generate(git_commit: str | None = None) -> dict[str, Any]:
    tests = {
        name: _run_test_suite(name, command)
        for name, command in TEST_COMMANDS
    }
    test_total = sum(item["total"] for item in tests.values())
    test_passed = sum(item["passed"] for item in tests.values())
    report = _run_static_snapshot()
    evaluation = _run_evaluation()
    evaluation_summary = evaluation["summary"]

    passed_seconds = round(
        sum(
            float(record.get("duration_ms", 0))
            for record in report["records"]
            if record.get("status") == "PASSED"
        )
        / 1000,
        3,
    )
    blocked_seconds = round(
        sum(
            float(record.get("duration_ms", 0))
            for record in report["records"]
            if record.get("status") == "BLOCKED"
        )
        / 1000,
        3,
    )

    evidence = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_commit(git_commit),
        "pipeline_version": report["pipeline_version"],
        "rules_version": report["rules_version"],
        "tests": {
            "passed": test_passed,
            "total": test_total,
            "suites": tests,
        },
        "evaluation": {
            "status_correct": evaluation_summary["status_correct"],
            "status_total": evaluation_summary["total_cases"],
            "status_accuracy": evaluation_summary["status_accuracy"],
            "anomalies_detected": evaluation_summary["detected_anomalies"],
            "anomalies_total": evaluation_summary["expected_anomalies"],
            "anomaly_detection_rate": evaluation_summary["anomaly_detection_rate"],
            "rule_codes_correct": evaluation_summary["rule_code_correct"],
            "rule_codes_total": evaluation_summary["total_cases"],
            "rule_code_match_rate": evaluation_summary["rule_code_match_rate"],
        },
        "e2e": {
            "mode": "static_snapshot",
            "passed_duration_seconds": passed_seconds,
            "blocked_duration_seconds": blocked_seconds,
            "idempotency": {
                "admin": "SKIPPED_UNCHANGED",
                "public": "REEXECUTES_EVERY_TIME",
            },
        },
        "sample_note": (
            "基于 10 条定向项目评测集，其中 6 条为边界异常任务。"
            "本文件记录静态快照；公开实时运行会重新读取固定 Base、执行规则和 DeepSeek，"
            "且不会上传或回写飞书。"
        ),
    }
    PUBLIC_EVIDENCE_PATH.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="生成公开 Demo 的流水线快照和脱敏证据")
    parser.add_argument(
        "--git-commit",
        help="证据所属提交短 SHA；省略时读取当前 HEAD",
    )
    args = parser.parse_args()
    evidence = generate(args.git_commit)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(f"快照: {PIPELINE_PATH}")
    print(f"证据: {PUBLIC_EVIDENCE_PATH}")


if __name__ == "__main__":
    main()
