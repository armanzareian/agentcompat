from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

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

    def test_summarizes_weighted_risk_by_tool(self) -> None:
        baseline: dict[str, dict[str, Any]] = {
            "lookup": {"type": "object"},
            "search": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "tenant_id": {"type": "string"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }
        candidate: dict[str, dict[str, Any]] = {
            "lookup": {"type": "object"},
            "search": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "tenant_id": {"type": "string"},
                },
                "required": ["query", "tenant_id"],
                "additionalProperties": False,
            },
        }
        traces = [
            ToolCall("search-pass", "search", {"query": "orders", "tenant_id": "a"}, 3),
            ToolCall("search-break", "search", {"query": "orders"}, 2),
            ToolCall("search-excluded", "search", {"query": "orders", "extra": True}, 5),
            ToolCall("lookup-pass", "lookup", {}, 4),
        ]

        report = analyze_compatibility(baseline, candidate, traces)

        self.assertEqual(["search", "lookup"], [summary.tool for summary in report.tool_summaries])
        search = report.tool_summaries[0]
        self.assertEqual(60.0, search.score)
        self.assertEqual(1, search.passed)
        self.assertEqual(1, search.broken)
        self.assertEqual(1, search.excluded)
        self.assertEqual(5.0, search.eligible_weight)
        self.assertEqual(3.0, search.passing_weight)
        self.assertEqual(2.0, search.risk_weight)
        self.assertEqual(5.0, search.excluded_weight)

    def test_samples_weighted_tool_strata_deterministically(self) -> None:
        baseline: dict[str, dict[str, Any]] = {
            "lookup": {"type": "object"},
            "search": {"type": "object"},
        }
        candidate: dict[str, dict[str, Any]] = {
            "lookup": {"type": "object"},
            "search": {"type": "object"},
        }
        traces = [
            ToolCall("search-heavy", "search", {"query": "orders"}, 100),
            ToolCall("search-light-1", "search", {"query": "orders"}, 1),
            ToolCall("search-light-2", "search", {"query": "orders"}, 1),
            ToolCall("lookup-1", "lookup", {}, 1),
            ToolCall("lookup-2", "lookup", {}, 1),
        ]

        first = analyze_compatibility(
            baseline,
            candidate,
            traces,
            sample_size=3,
            sample_seed=17,
        )
        second = analyze_compatibility(
            baseline,
            candidate,
            traces,
            sample_size=3,
            sample_seed=17,
        )

        self.assertEqual(
            [result.trace.trace_id for result in first.results],
            [result.trace.trace_id for result in second.results],
        )
        self.assertEqual(3, len(first.results))
        self.assertIn("search-heavy", [result.trace.trace_id for result in first.results])
        assert first.sampling is not None
        sampling = first.sampling
        self.assertEqual(
            {"search": 2, "lookup": 1},
            {summary.tool: summary.sampled for summary in sampling.strata},
        )
        self.assertEqual(5, sampling.population)
        self.assertEqual(104.0, sampling.population_weight)
        self.assertEqual(3, sampling.sampled)
        self.assertEqual(17, sampling.seed)

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

    def test_attributes_failures_and_ranks_deduplicated_migrations(self) -> None:
        baseline = {
            "search": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open", "closed"]},
                    "customer_id": {"type": "string"},
                    "include_archived": {"type": "boolean"},
                    "limit": {"type": "integer", "maximum": 100},
                },
                "required": ["status"],
                "additionalProperties": False,
            }
        }
        candidate = {
            "search": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["open"]},
                    "customer_id": {"type": "string"},
                    "limit": {"type": "integer", "maximum": 50},
                },
                "required": ["status", "customer_id"],
                "additionalProperties": False,
            }
        }
        traces = [
            ToolCall("enum", "search", {"status": "closed", "customer_id": "c1"}, 4),
            ToolCall("required", "search", {"status": "open"}, 3),
            ToolCall(
                "property",
                "search",
                {"status": "open", "customer_id": "c2", "include_archived": True},
                2,
            ),
            ToolCall("limit", "search", {"status": "open", "customer_id": "c3", "limit": 75}, 1),
            ToolCall("pass", "search", {"status": "open", "customer_id": "c4"}, 5),
        ]

        report = analyze_compatibility(baseline, candidate, traces)

        broken = {result.trace.trace_id: result for result in report.results if result.issues}
        self.assertTrue(
            all(issue.change_ids for result in broken.values() for issue in result.issues)
        )
        self.assertEqual(
            ["enum_narrowed", "required_added", "property_removed", "constraint_tightened"],
            [item.kind for item in report.migration_plan],
        )
        self.assertEqual(
            [4.0, 3.0, 2.0, 1.0], [item.affected_weight for item in report.migration_plan]
        )
        self.assertEqual(("enum",), report.migration_plan[0].trace_ids)

    def test_attributes_tightened_additional_properties_inside_arrays(self) -> None:
        baseline = {
            "batch": {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"code": {"type": "string"}},
                        },
                    }
                },
            }
        }
        candidate = {
            "batch": {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"code": {"type": "string"}},
                            "additionalProperties": False,
                        },
                    }
                },
            }
        }

        report = analyze_compatibility(
            baseline,
            candidate,
            [ToolCall("trace-1", "batch", {"rows": [{"code": "A", "extra": 1}]})],
        )

        issue = report.results[0].issues[0]
        attributed = next(
            change for change in report.changes if change.change_id == issue.change_ids[0]
        )
        self.assertEqual("$.rows[0].extra", issue.path)
        self.assertEqual("$.rows[*]", attributed.path)
        self.assertEqual("additionalProperties", attributed.keyword)


if __name__ == "__main__":
    unittest.main()
