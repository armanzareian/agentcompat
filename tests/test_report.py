from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.models import (
    CompatibilityReport,
    MigrationPlanItem,
    SamplingStratum,
    SamplingSummary,
    SchemaChange,
    ScoreConfidenceInterval,
    ToolCall,
    ToolSummary,
    TraceResult,
    ValidationIssue,
)
from agentcompat.report import render_text, report_to_dict


class ReportTests(unittest.TestCase):
    def test_renders_failures_and_skips_passed_trace_details(self) -> None:
        change = SchemaChange(
            change_id="chg_required",
            kind="required_added",
            tool="search",
            path="$.query",
            keyword="required",
            before=False,
            after=True,
            description="$.query became required for tool 'search'.",
        )
        migration = MigrationPlanItem(
            change_id="chg_required",
            kind="required_added",
            tool="search",
            path="$.query",
            keyword="required",
            affected_weight=1.0,
            trace_ids=("broken",),
            issue_count=1,
            guidance="Populate $.query for search calls.",
        )
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
                            change_ids=("chg_required",),
                        ),
                    ),
                    ("Provide query.",),
                ),
            ),
            changes=(change,),
            migration_plan=(migration,),
            tool_summaries=(
                ToolSummary(
                    tool="search",
                    score=50.0,
                    passed=1,
                    broken=1,
                    excluded=0,
                    eligible_weight=2.0,
                    passing_weight=1.0,
                    risk_weight=1.0,
                    excluded_weight=0.0,
                ),
            ),
            sampling=SamplingSummary(
                requested_size=5,
                seed=17,
                population=10,
                sampled=5,
                population_weight=20.0,
                sampled_weight=12.0,
                strata=(
                    SamplingStratum(
                        tool="search",
                        population=10,
                        sampled=5,
                        population_weight=20.0,
                        sampled_weight=12.0,
                    ),
                ),
            ),
            confidence_interval=ScoreConfidenceInterval(
                metric="score",
                confidence_level=0.9,
                lower=40.0,
                upper=75.0,
                iterations=200,
                seed=17,
            ),
        )

        rendered = render_text(report)
        payload = report_to_dict(report)

        self.assertIn("Score: 50.00/100", rendered)
        self.assertIn("Score confidence interval: 40.00-75.00", rendered)
        self.assertIn("Sampling: 5/10 calls selected with seed 17", rendered)
        self.assertNotIn("PASSED passed", rendered)
        self.assertIn("BROKEN broken", rendered)
        self.assertIn("Hint: Provide query.", rendered)
        self.assertIn("Change: chg_required", rendered)
        self.assertIn("Migration plan", rendered)
        self.assertIn("1. [required_added] search $.query", rendered)
        self.assertIn("Tool risk", rendered)
        self.assertIn("- search: 50.00/100", rendered)
        self.assertEqual("missing_required", payload["results"][1]["issues"][0]["code"])
        self.assertEqual(
            {
                "tool": "search",
                "score": 50.0,
                "passed": 1,
                "broken": 1,
                "excluded": 0,
                "eligible_weight": 2.0,
                "passing_weight": 1.0,
                "risk_weight": 1.0,
                "excluded_weight": 0.0,
            },
            payload["tools"][0],
        )
        self.assertEqual(
            ["chg_required"],
            payload["results"][1]["issues"][0]["change_ids"],
        )
        self.assertEqual("chg_required", payload["changes"][0]["change_id"])
        self.assertEqual(
            {
                "rank": 1,
                "change_id": "chg_required",
                "kind": "required_added",
                "tool": "search",
                "path": "$.query",
                "keyword": "required",
                "affected_weight": 1.0,
                "affected_traces": 1,
                "trace_ids": ["broken"],
                "issue_count": 1,
                "guidance": "Populate $.query for search calls.",
            },
            payload["migration_plan"][0],
        )
        self.assertEqual(
            {
                "requested_size": 5,
                "seed": 17,
                "population": 10,
                "sampled": 5,
                "population_weight": 20.0,
                "sampled_weight": 12.0,
                "strata": [
                    {
                        "tool": "search",
                        "population": 10,
                        "sampled": 5,
                        "population_weight": 20.0,
                        "sampled_weight": 12.0,
                    }
                ],
            },
            payload["sampling"],
        )
        self.assertEqual(
            {
                "metric": "score",
                "confidence_level": 0.9,
                "lower": 40.0,
                "upper": 75.0,
                "iterations": 200,
                "seed": 17,
            },
            payload["confidence_interval"],
        )


if __name__ == "__main__":
    unittest.main()
