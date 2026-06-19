from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.io import InputError, RedactionConfig, read_traces


class TraceAdapterTests(unittest.TestCase):
    def test_openai_response_events_redact_before_returning_tool_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "openai.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "type": "response.output_item.done",
                        "weight": 2,
                        "item": {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "search_customers",
                            "arguments": json.dumps(
                                {
                                    "email": "ada@example.com",
                                    "metadata": {
                                        "api_key": "sensitive-api-value",
                                        "tenant": "north",
                                    },
                                }
                            ),
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            traces = read_traces(
                path,
                trace_format="openai",
                redaction=RedactionConfig(
                    paths=("$.email",),
                    key_patterns=("api[_-]?key",),
                ),
            )

        self.assertEqual(1, len(traces))
        self.assertEqual("call-1", traces[0].trace_id)
        self.assertEqual("search_customers", traces[0].tool)
        self.assertEqual(2.0, traces[0].weight)
        self.assertEqual("[REDACTED]", traces[0].arguments["email"])
        self.assertEqual("[REDACTED]", traces[0].arguments["metadata"]["api_key"])
        self.assertEqual("north", traces[0].arguments["metadata"]["tenant"])

    def test_provider_adapters_emit_the_same_canonical_model(self) -> None:
        cases = {
            "anthropic": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu-1",
                        "name": "lookup_order",
                        "input": {"order_id": "ord-1"},
                    }
                ]
            },
            "mcp": {
                "weight": 3,
                "request": {
                    "jsonrpc": "2.0",
                    "id": "rpc-1",
                    "method": "tools/call",
                    "params": {
                        "name": "lookup_order",
                        "arguments": {"order_id": "ord-1"},
                    },
                },
            },
            "langchain": {
                "event": "on_tool_start",
                "run_id": "run-1",
                "name": "lookup_order",
                "data": {"input": {"order_id": "ord-1"}},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for trace_format, payload in cases.items():
                with self.subTest(trace_format=trace_format):
                    path = root / f"{trace_format}.jsonl"
                    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

                    traces = read_traces(path, trace_format=trace_format)

                    self.assertEqual(1, len(traces))
                    self.assertEqual("lookup_order", traces[0].tool)
                    self.assertEqual({"order_id": "ord-1"}, traces[0].arguments)
                    if trace_format == "mcp":
                        self.assertEqual(3.0, traces[0].weight)

    def test_streaming_adapters_ignore_non_tool_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "openai-stream.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "response.created", "response": {"id": "resp-1"}}),
                        json.dumps(
                            {
                                "type": "response.output_item.done",
                                "item": {
                                    "type": "function_call",
                                    "call_id": "call-1",
                                    "name": "lookup_order",
                                    "arguments": json.dumps({"order_id": "ord-1"}),
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            traces = read_traces(path, trace_format="openai")

        self.assertEqual(1, len(traces))
        self.assertEqual("call-1", traces[0].trace_id)
        self.assertEqual({"order_id": "ord-1"}, traces[0].arguments)

    def test_adapter_errors_name_source_field_without_payload_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "openai.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "type": "response.output_item.done",
                        "item": {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "search",
                            "arguments": '{"token": "sensitive-value",',
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(InputError, "line 1 item.arguments") as context:
                read_traces(path, trace_format="openai")

        message = str(context.exception)
        self.assertNotIn("sensitive-value", message)
        self.assertNotIn("token", message)


if __name__ == "__main__":
    unittest.main()
