from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.io import InputError, parse_tool_bundle, read_traces


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
