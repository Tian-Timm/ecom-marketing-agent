from __future__ import annotations

import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from evaluate_fixture import evaluate_fixture


class EvaluationTests(unittest.TestCase):
    def test_fixture_matches_independent_ground_truth(self) -> None:
        report = evaluate_fixture()
        summary = report["summary"]

        self.assertEqual(summary["total_cases"], 10)
        self.assertEqual(summary["expected_anomalies"], 6)
        self.assertEqual(summary["status_correct"], 10)
        self.assertEqual(summary["status_accuracy"], 1.0)
        self.assertEqual(summary["detected_anomalies"], 6)
        self.assertEqual(summary["anomaly_detection_rate"], 1.0)
        self.assertEqual(summary["rule_code_correct"], 10)
        self.assertEqual(summary["rule_code_match_rate"], 1.0)
        self.assertEqual(report["mismatches"], [])


if __name__ == "__main__":
    unittest.main()
