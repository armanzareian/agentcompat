from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.io import (
    InputError,
    iter_traces,
    load_tool_bundle,
    parse_tool_bundle,
    read_traces,
)


class ToolBundleTests(unittest.TestCase):
    def test_normalizes_mcp_and_openai_tool_shapes(self) -> None:
        bundle = {
            "tools": [
                {
                    "name": "lookup_customer",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "type": "function",
                    "function": {
                        "name": "search_orders",
                        "parameters": {"type": "object", "required": ["status"]},
                    },
                },
            ]
        }

        tools = parse_tool_bundle(bundle)

        self.assertEqual(
            {"lookup_customer", "search_orders"},
            set(tools),
        )
        self.assertEqual(["status"], tools["search_orders"]["required"])

    def test_resolves_internal_and_local_file_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "shared.json").write_text(
                json.dumps(
                    {
                        "$defs": {
                            "query": {
                                "type": "string",
                                "pattern": "^[a-z]+$",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            bundle = root / "bundle.json"
            bundle.write_text(
                json.dumps(
                    {
                        "$defs": {"limit": {"type": "integer", "minimum": 1}},
                        "tools": [
                            {
                                "name": "search",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {
                                            "$ref": "shared.json#/$defs/query",
                                        },
                                        "limit": {"$ref": "#/$defs/limit"},
                                    },
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            tools = load_tool_bundle(bundle)

        self.assertEqual(
            {"type": "string", "pattern": "^[a-z]+$"},
            tools["search"]["properties"]["query"],
        )
        self.assertEqual(
            {"type": "integer", "minimum": 1},
            tools["search"]["properties"]["limit"],
        )

    def test_rejects_reference_traversal_outside_schema_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root.parent / "outside-agentcompat-schema.json"
            outside.write_text('{"type":"string"}', encoding="utf-8")
            bundle = root / "bundle.json"
            bundle.write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "search",
                                "inputSchema": {"$ref": "../outside-agentcompat-schema.json"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            try:
                with self.assertRaisesRegex(InputError, "outside schema root"):
                    load_tool_bundle(bundle)
            finally:
                outside.unlink(missing_ok=True)

    def test_rejects_reference_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle.json"
            bundle.write_text(
                json.dumps(
                    {
                        "$defs": {
                            "a": {"$ref": "#/$defs/b"},
                            "b": {"$ref": "#/$defs/a"},
                        },
                        "tools": [
                            {
                                "name": "search",
                                "inputSchema": {"$ref": "#/$defs/a"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(InputError, "cycle"):
                load_tool_bundle(bundle)

    def test_rejects_invalid_reference_targets(self) -> None:
        cases = [
            ("https://example.com/schema.json", "Only local file"),
            ("#named-anchor", "non-pointer fragment"),
            ("#/$defs/missing", "missing location"),
            ("#/$defs/value", "does not point to a schema"),
            ("#/$defs/values/4", "outside an array"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle.json"
            for reference, message in cases:
                with self.subTest(reference=reference):
                    bundle.write_text(
                        json.dumps(
                            {
                                "$defs": {
                                    "value": "not-a-schema",
                                    "values": [{"type": "string"}],
                                },
                                "tools": [
                                    {
                                        "name": "search",
                                        "inputSchema": {"$ref": reference},
                                    }
                                ],
                            }
                        ),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(InputError, message):
                        load_tool_bundle(bundle)

            bundle.write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "search",
                                "inputSchema": {"$ref": 42},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(InputError, "must be strings"):
                load_tool_bundle(bundle)


class TraceReaderTests(unittest.TestCase):
    def test_reads_canonical_jsonl_traces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "trace_id": "trace-1",
                        "tool": "search_orders",
                        "arguments": {"status": "open"},
                        "weight": 2.5,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            traces = read_traces(path)

        self.assertEqual("trace-1", traces[0].trace_id)
        self.assertEqual(2.5, traces[0].weight)

    def test_iter_traces_yields_records_before_later_parse_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.jsonl"
            path.write_text(
                (
                    '{"trace_id":"trace-1","tool":"search","arguments":{"query":"x"}}\n'
                    "{not json}\n"
                ),
                encoding="utf-8",
            )

            traces = iter_traces(path)
            first = next(traces)

            self.assertEqual("trace-1", first.trace_id)
            with self.assertRaisesRegex(InputError, "Invalid JSON on line 2"):
                next(traces)

    def test_rejects_non_positive_trace_weights(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.jsonl"
            path.write_text(
                '{"trace_id":"trace-1","tool":"x","arguments":{},"weight":0}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(InputError, "weight"):
                read_traces(path)


if __name__ == "__main__":
    unittest.main()
