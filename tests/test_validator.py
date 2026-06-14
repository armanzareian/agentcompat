from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.validator import audit_schema, validate_instance


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

    def test_audits_unsupported_keywords_and_ignores_annotations(self) -> None:
        schema = {
            "$id": "https://example.com/schema",
            "type": "array",
            "contains": {"type": "string"},
            "description": "A documented value.",
            "x-provider-note": "metadata",
        }

        issues = audit_schema(schema)

        self.assertEqual(
            [("$id", '$["$id"]'), ("contains", "$.contains")],
            [(issue.keyword, issue.schema_path) for issue in issues],
        )

    def test_audits_unsupported_type_and_unresolved_reference_semantics(self) -> None:
        schema = {
            "allOf": [
                {"type": "decimal"},
                {"$ref": "#/$defs/value"},
            ],
            "$defs": {"value": {"type": "number"}},
        }

        issues = audit_schema(schema)

        self.assertEqual(
            [
                ("type:decimal", "$.allOf[0].type"),
                ("$ref:unresolved", '$.allOf[1]["$ref"]'),
            ],
            [(issue.keyword, issue.schema_path) for issue in issues],
        )

    def test_validates_pattern_and_common_formats(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "code": {"type": "string", "pattern": "^[A-Z]{3}$"},
                "created_at": {"type": "string", "format": "date-time"},
                "address": {"type": "string", "format": "ipv4"},
            },
        }

        issues = validate_instance(
            {
                "code": "lower",
                "created_at": "2026-13-40",
                "address": "999.1.1.1",
            },
            schema,
        )

        self.assertEqual(
            [
                ("pattern_mismatch", "$.code"),
                ("format_mismatch", "$.created_at"),
                ("format_mismatch", "$.address"),
            ],
            [(issue.code, issue.path) for issue in issues],
        )

    def test_applies_conditional_schema_branch(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "kind": {"enum": ["email", "sms"]},
                "address": {"type": "string"},
                "phone": {"type": "string"},
            },
            "if": {
                "properties": {"kind": {"const": "email"}},
                "required": ["kind"],
            },
            "then": {"required": ["address"]},
            "else": {"required": ["phone"]},
        }

        email_issues = validate_instance({"kind": "email"}, schema)
        sms_issues = validate_instance({"kind": "sms"}, schema)

        self.assertEqual("$.address", email_issues[0].path)
        self.assertEqual("$.phone", sms_issues[0].path)

    def test_validates_prefix_and_legacy_tuple_items(self) -> None:
        prefix_schema = {
            "type": "array",
            "prefixItems": [{"type": "string"}, {"type": "integer"}],
            "items": False,
        }
        legacy_schema = {
            "type": "array",
            "items": [{"type": "string"}, {"type": "integer"}],
            "additionalItems": False,
        }

        prefix_issues = validate_instance(["ok", "wrong", "extra"], prefix_schema)
        legacy_issues = validate_instance(["ok", "wrong", "extra"], legacy_schema)

        self.assertEqual(
            [("type_mismatch", "$[1]"), ("false_schema", "$[2]")],
            [(issue.code, issue.path) for issue in prefix_issues],
        )
        self.assertEqual(
            [("type_mismatch", "$[1]"), ("false_schema", "$[2]")],
            [(issue.code, issue.path) for issue in legacy_issues],
        )

    def test_validates_multiple_of_and_unique_items(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "ratio": {"type": "number", "multipleOf": 0.25},
                "labels": {"type": "array", "uniqueItems": True},
            },
        }

        issues = validate_instance(
            {"ratio": 0.3, "labels": ["a", {"x": 1}, {"x": 1}]},
            schema,
        )

        self.assertEqual(
            [("multiple_of", "$.ratio"), ("unique_items", "$.labels")],
            [(issue.code, issue.path) for issue in issues],
        )


if __name__ == "__main__":
    unittest.main()
