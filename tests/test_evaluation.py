from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.evaluation import evaluate_report
from agentcompat.models import (
    CompatibilityReport,
    ToolCall,
    TraceResult,
    ValidationIssue,
)


class EvaluateReportTests(unittest.TestCase):
    def test_computes_trace_detection_and_root_cause_metrics(self) -> None:
        report = CompatibilityReport(
            score=50.0,
            passed=1,
            broken=2,
            excluded=0,
            eligible_weight=3.0,
            passing_weight=1.0,
            results=(
                TraceResult(
                    ToolCall("true-positive", "search", {}, 1),
                    "broken",
                    (ValidationIssue("enum_mismatch", "$.status", "bad enum"),),
                ),
                TraceResult(
                    ToolCall("false-positive", "search", {}, 1),
                    "broken",
                    (ValidationIssue("type_mismatch", "$.limit", "bad type"),),
                ),
                TraceResult(ToolCall("pass", "search", {}, 1), "passed"),
            ),
        )

        metrics = evaluate_report(
            report,
            {
                "true-positive": ["enum_mismatch"],
                "false-negative": ["missing_required"],
            },
        )

        self.assertEqual(0.5, metrics.precision)
        self.assertEqual(0.5, metrics.recall)
        self.assertEqual(0.5, metrics.f1)
        self.assertEqual(0.5, metrics.root_cause_accuracy)


if __name__ == "__main__":
    unittest.main()
