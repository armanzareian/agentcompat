from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.changes import build_migration_plan, compare_tool_bundles
from agentcompat.models import SchemaChange, ToolCall, TraceResult, ValidationIssue


class CompareToolBundlesTests(unittest.TestCase):
    def test_detects_breaking_changes_with_stable_content_ids(self) -> None:
        baseline: dict[str, dict[str, Any]] = {
            "search": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "status": {"type": "string", "enum": ["open", "closed"]},
                    "legacy": {"type": "boolean"},
                    "limit": {"type": "integer", "maximum": 100},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            "legacy_tool": {"type": "object"},
        }
        candidate: dict[str, dict[str, Any]] = {
            "search": {
                "additionalProperties": False,
                "required": ["query", "tenant_id"],
                "properties": {
                    "tenant_id": {"type": "string"},
                    "limit": {"maximum": 50, "type": "integer"},
                    "status": {"enum": ["open"], "type": "string"},
                    "query": {"type": "integer"},
                },
                "type": "object",
            }
        }

        changes = compare_tool_bundles(baseline, candidate)
        reordered = compare_tool_bundles(
            {"legacy_tool": baseline["legacy_tool"], "search": baseline["search"]},
            candidate,
        )

        self.assertEqual(
            {
                ("constraint_tightened", "search", "$.limit", "maximum"),
                ("enum_narrowed", "search", "$.status", "enum"),
                ("property_removed", "search", "$.legacy", "properties"),
                ("required_added", "search", "$.tenant_id", "required"),
                ("tool_removed", "legacy_tool", "$", "tool"),
                ("type_changed", "search", "$.query", "type"),
            },
            {(change.kind, change.tool, change.path, change.keyword) for change in changes},
        )
        self.assertEqual(
            [change.change_id for change in changes],
            [change.change_id for change in reordered],
        )
        self.assertEqual(len(changes), len({change.change_id for change in changes}))
        self.assertTrue(all(change.change_id.startswith("chg_") for change in changes))

    def test_detects_nested_and_added_constraints_without_flagging_type_widening(self) -> None:
        baseline = {
            "batch": {
                "type": "object",
                "properties": {
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {"type": ["string", "null"], "minLength": 1},
                                "count": {"type": "integer"},
                                "tags": {"type": "array"},
                                "name": {"type": "string"},
                            },
                        },
                    },
                    "tuple": {
                        "type": "array",
                        "prefixItems": [{"type": "string", "maxLength": 10}],
                    },
                    "blocked": True,
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
                            "additionalProperties": False,
                            "properties": {
                                "code": {"type": "string", "minLength": 2},
                                "count": {"type": "number", "multipleOf": 5},
                                "tags": {"type": "array", "uniqueItems": True},
                                "name": {"type": "string", "pattern": "^[A-Z]"},
                            },
                        },
                    },
                    "tuple": {
                        "type": "array",
                        "prefixItems": [{"type": "string", "maxLength": 5}],
                    },
                    "blocked": False,
                },
            }
        }

        changes = compare_tool_bundles(baseline, candidate)
        keys = {(change.path, change.keyword) for change in changes}

        self.assertIn(("$.rows[*].code", "type"), keys)
        self.assertIn(("$.rows[*].code", "minLength"), keys)
        self.assertIn(("$.rows[*].count", "multipleOf"), keys)
        self.assertIn(("$.rows[*].tags", "uniqueItems"), keys)
        self.assertIn(("$.rows[*].name", "pattern"), keys)
        self.assertIn(("$.rows[*]", "additionalProperties"), keys)
        self.assertIn(("$.tuple[0]", "maxLength"), keys)
        self.assertIn(("$.blocked", "schema"), keys)
        self.assertNotIn(("$.rows[*].count", "type"), keys)

    def test_migration_plan_counts_repeated_issue_once_per_trace_weight(self) -> None:
        change = SchemaChange(
            "chg_limit",
            "constraint_tightened",
            "search",
            "$.limit",
            "maximum",
            100,
            50,
            "Limit tightened.",
        )
        issue = ValidationIssue(
            "maximum",
            "$.limit",
            "Too high.",
            change_ids=("chg_limit", "chg_limit"),
        )

        plan = build_migration_plan(
            (change,),
            (
                TraceResult(
                    ToolCall("trace-1", "search", {"limit": 75}, 3),
                    "broken",
                    (issue,),
                ),
            ),
        )

        self.assertEqual(3.0, plan[0].affected_weight)
        self.assertEqual(("trace-1",), plan[0].trace_ids)
        self.assertEqual(2, plan[0].issue_count)

    def test_deduplicates_identical_changes_from_composed_schemas(self) -> None:
        baseline = {
            "search": {
                "allOf": [
                    {"type": "object", "maxProperties": 10},
                    {"type": "object", "maxProperties": 10},
                ]
            }
        }
        candidate = {
            "search": {
                "allOf": [
                    {"type": "object", "maxProperties": 5},
                    {"type": "object", "maxProperties": 5},
                ]
            }
        }

        changes = compare_tool_bundles(baseline, candidate)

        self.assertEqual(1, len(changes))
        self.assertEqual("maxProperties", changes[0].keyword)


if __name__ == "__main__":
    unittest.main()
