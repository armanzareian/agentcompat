from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.validator import validate_instance


class ValidateInstanceTests(unittest.TestCase):
    def test_reports_nested_missing_required_field(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "properties": {"customer_id": {"type": "string"}},
                    "required": ["customer_id"],
                }
            },
        }

        issues = validate_instance({"filter": {}}, schema)

        self.assertEqual("missing_required", issues[0].code)
        self.assertEqual("$.filter.customer_id", issues[0].path)

    def test_reports_enum_and_unexpected_property(self) -> None:
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["open", "fulfilled"]}},
            "required": ["status"],
            "additionalProperties": False,
        }

        issues = validate_instance({"status": "closed", "legacy": True}, schema)

        self.assertEqual(
            [("enum_mismatch", "$.status"), ("unexpected_property", "$.legacy")],
            [(issue.code, issue.path) for issue in issues],
        )

    def test_supports_array_constraints_and_any_of(self) -> None:
        schema = {
            "type": "array",
            "minItems": 2,
            "items": {
                "anyOf": [
                    {"type": "integer"},
                    {"type": "string", "minLength": 3},
                ]
            },
        }

        issues = validate_instance(["ok"], schema)

        self.assertEqual(
            ["min_items", "any_of_mismatch"],
            [issue.code for issue in issues],
        )

    def test_returns_unsupported_keywords_without_failing_the_instance(self) -> None:
        schema = {
            "type": "string",
            "pattern": "^[A-Z]+$",
            "x-provider-note": "metadata",
        }

        issues = validate_instance("lowercase", schema)

        self.assertEqual([], issues)


if __name__ == "__main__":
    unittest.main()
