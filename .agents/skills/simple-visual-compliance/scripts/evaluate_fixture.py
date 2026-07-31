#!/usr/bin/env python3
"""使用独立真值集评测营销任务流水线。"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

from run_pipeline import run_pipeline


SKILL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_DIR.parents[2]
DEFAULT_INPUT = SKILL_DIR / "assets" / "marketing_tasks.csv"
DEFAULT_GROUND_TRUTH = SKILL_DIR / "assets" / "evaluation_ground_truth.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "generated_output" / "evaluation_result.json"


def load_ground_truth(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("评测真值必须包含非空 cases 数组")
    return payload


def evaluate_fixture(
    input_path: Path = DEFAULT_INPUT,
    ground_truth_path: Path = DEFAULT_GROUND_TRUTH,
) -> Dict[str, Any]:
    ground_truth = load_ground_truth(ground_truth_path)
    started_at = time.perf_counter()
    with tempfile.TemporaryDirectory() as temp_dir:
        pipeline_report = run_pipeline(input_path, Path(temp_dir))
    duration_seconds = round(time.perf_counter() - started_at, 3)

    actual_by_id = {
        str(record["task_id"]): record
        for record in pipeline_report["records"]
    }
    case_results = []
    status_correct = 0
    rule_code_correct = 0
    expected_anomalies = 0
    detected_anomalies = 0

    for expected in ground_truth["cases"]:
        task_id = str(expected["task_id"])
        expected_status = str(expected["expected_status"])
        expected_codes = sorted(expected.get("expected_violation_codes", []))
        actual = actual_by_id.get(task_id)
        actual_status = str(actual.get("status")) if actual else "MISSING"
        actual_codes = sorted(
            violation["code"]
            for violation in (actual or {}).get("violations", [])
        )
        status_matches = actual_status == expected_status
        codes_match = actual_codes == expected_codes

        status_correct += int(status_matches)
        rule_code_correct += int(codes_match)
        if expected_status == "BLOCKED":
            expected_anomalies += 1
            detected_anomalies += int(actual_status == "BLOCKED")

        case_results.append({
            "task_id": task_id,
            "expected_status": expected_status,
            "actual_status": actual_status,
            "expected_violation_codes": expected_codes,
            "actual_violation_codes": actual_codes,
            "status_matches": status_matches,
            "rule_codes_match": codes_match,
        })

    total_cases = len(case_results)
    mismatches = [
        result
        for result in case_results
        if not result["status_matches"] or not result["rule_codes_match"]
    ]
    return {
        "schema_version": "1.0",
        "dataset": ground_truth.get("dataset", input_path.name),
        "rules_version": pipeline_report["rules_version"],
        "duration_seconds": duration_seconds,
        "summary": {
            "total_cases": total_cases,
            "expected_anomalies": expected_anomalies,
            "status_correct": status_correct,
            "status_accuracy": status_correct / total_cases,
            "detected_anomalies": detected_anomalies,
            "anomaly_detection_rate": (
                detected_anomalies / expected_anomalies
                if expected_anomalies
                else 1.0
            ),
            "rule_code_correct": rule_code_correct,
            "rule_code_match_rate": rule_code_correct / total_cases,
            "images_generated": pipeline_report["summary"]["images_generated"],
        },
        "mismatches": mismatches,
        "cases": case_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="运行带独立真值的规则评测")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    report = evaluate_fixture(args.input, args.ground_truth)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = report["summary"]
    print("评测完成")
    print(f"任务状态一致: {summary['status_correct']}/{summary['total_cases']}")
    print(
        "异常检出: "
        f"{summary['detected_anomalies']}/{summary['expected_anomalies']}"
    )
    print(
        "规则码一致: "
        f"{summary['rule_code_correct']}/{summary['total_cases']}"
    )
    print(f"耗时: {report['duration_seconds']:.3f} 秒")
    print(f"报告: {args.output}")


if __name__ == "__main__":
    main()
