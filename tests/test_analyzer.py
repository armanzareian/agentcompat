from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.analyzer import analyze_compatibility
from agentcompat.models import ToolCall


class AnalyzeCompatibilityTests(unittest.TestCase):
    def test_scores_only_baseline_valid_observed_calls(self) -> None:
        baseline = {
            "search_orders": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "closed"]},
                    "customer_id": {"type": "string"},
                },
                "required": ["status"],
                "additionalProperties": False,
            }
        }
        candidate = {
            "search_orders": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "fulfilled"]},
                    "customer_id": {"type": "string"},
                },
                "required": ["status", "customer_id"],
                "additionalProperties": False,
            }
        }
        traces = [
            ToolCall("pass", "search_orders", {"status": "open", "customer_id": "c1"}, 3),
            ToolCall("break", "search_orders", {"status": "closed"}, 1),
            ToolCall("excluded", "search_orders", {"unknown": True}, 10),
        ]

        report = analyze_compatibility(baseline, candidate, traces)

        self.assertEqual(75.0, report.score)
        self.assertEqual(1, report.passed)
        self.assertEqual(1, report.broken)
        self.assertEqual(1, report.excluded)
        self.assertEqual(
            ["enum_mismatch", "missing_required"],
            sorted(issue.code for issue in report.results[1].issues),
        )

    def test_reports_removed_candidate_tool_as_breaking(self) -> None:
        baseline = {"search": {"type": "object"}}
        traces = [ToolCall("trace-1", "search", {}, 1)]

        report = analyze_compatibility(baseline, {}, traces)

        self.assertEqual(0.0, report.score)
        self.assertEqual("tool_removed", report.results[0].issues[0].code)

    def test_produces_actionable_repair_hints(self) -> None:
        baseline = {"search": {"type": "object"}}
        candidate = {
            "search": {
                "type": "object",
                "properties": {"tenant_id": {"type": "string"}},
                "required": ["tenant_id"],
            }
        }

        report = analyze_compatibility(
            baseline,
            candidate,
            [ToolCall("trace-1", "search", {}, 1)],
        )

        self.assertIn("default", report.results[0].hints[0].lower())


if __name__ == "__main__":
    unittest.main()
