from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.models import CompatibilityReport, ToolCall, TraceResult, ValidationIssue
from agentcompat.report import render_text, report_to_dict


class ReportTests(unittest.TestCase):
    def test_renders_failures_and_skips_passed_trace_details(self) -> None:
        report = CompatibilityReport(
            score=50.0,
            passed=1,
            broken=1,
            excluded=0,
            eligible_weight=2.0,
            passing_weight=1.0,
            results=(
                TraceResult(ToolCall("passed", "search", {}, 1), "passed"),
                TraceResult(
                    ToolCall("broken", "search", {}, 1),
                    "broken",
                    (
                        ValidationIssue(
                            "missing_required",
                            "$.query",
                            "Required property is missing.",
                        ),
                    ),
                    ("Provide query.",),
                ),
            ),
        )

        rendered = render_text(report)
        payload = report_to_dict(report)

        self.assertIn("Score: 50.00/100", rendered)
        self.assertNotIn("PASSED passed", rendered)
        self.assertIn("BROKEN broken", rendered)
        self.assertIn("Hint: Provide query.", rendered)
        self.assertEqual("missing_required", payload["results"][1]["issues"][0]["code"])


if __name__ == "__main__":
    unittest.main()
