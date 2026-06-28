from __future__ import annotations

import importlib.resources
import json
import sys
import tempfile
import unittest
from collections.abc import Iterable
from pathlib import Path
from typing import cast

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat import ExtensionRegistry, SchemaSource, ToolBundle, ToolCall, TraceAdapter
from agentcompat.analyzer import analyze_compatibility
from agentcompat.io import InputError, RedactionConfig, load_tool_bundle, read_traces


class ExtensionApiTests(unittest.TestCase):
    def test_custom_trace_adapter_participates_in_redaction_and_replay(self) -> None:
        def acme_adapter(payload: object, line_number: int) -> Iterable[ToolCall]:
            if not isinstance(payload, dict):
                return ()
            calls = payload.get("calls")
            if not isinstance(calls, list):
                return ()
            traces: list[ToolCall] = []
            for index, call in enumerate(calls):
                if isinstance(call, dict):
                    traces.append(
                        ToolCall(
                            f"line-{line_number}-acme-{index}",
                            str(call["name"]),
                            dict(call["args"]),
                            float(call.get("weight", 1.0)),
                        )
                    )
            return tuple(traces)

        registry = ExtensionRegistry().with_trace_adapter("acme", acme_adapter)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acme.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "calls": [
                            {
                                "name": "lookup_customer",
                                "args": {
                                    "customer_id": "cus-1",
                                    "token": "sensitive",
                                },
                                "weight": 2,
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            traces = read_traces(
                path,
                trace_format="acme",
                redaction=RedactionConfig(key_patterns=("token",)),
                extensions=registry,
            )

        self.assertEqual(1, len(traces))
        self.assertEqual("line-1-acme-0", traces[0].trace_id)
        self.assertEqual("lookup_customer", traces[0].tool)
        self.assertEqual(2.0, traces[0].weight)
        self.assertEqual("[REDACTED]", traces[0].arguments["token"])

        report = analyze_compatibility(
            {"lookup_customer": {"type": "object"}},
            {"lookup_customer": {"type": "object"}},
            traces,
        )
        self.assertEqual(100.0, report.score)

    def test_custom_schema_source_returns_typed_tool_bundle(self) -> None:
        def in_memory_source(path: Path) -> ToolBundle:
            if path.name == "baseline.acme":
                return {"search": {"type": "object"}}
            return {
                "search": {
                    "type": "object",
                    "properties": {"tenant_id": {"type": "string"}},
                    "required": ["tenant_id"],
                }
            }

        registry = ExtensionRegistry().with_schema_source("acme", in_memory_source)

        baseline = load_tool_bundle(
            Path("baseline.acme"),
            schema_source="acme",
            extensions=registry,
        )
        candidate = load_tool_bundle(
            Path("candidate.acme"),
            schema_source="acme",
            extensions=registry,
        )
        report = analyze_compatibility(baseline, candidate, [ToolCall("trace-1", "search", {}, 1)])

        self.assertEqual(0.0, report.score)
        self.assertEqual("missing_required", report.results[0].issues[0].code)

    def test_package_exports_typed_extension_contract(self) -> None:
        def adapter(payload: object, line_number: int) -> Iterable[ToolCall]:
            del payload, line_number
            return ()

        def source(path: Path) -> ToolBundle:
            del path
            return {"search": {"type": "object"}}

        trace_adapter: TraceAdapter = adapter
        schema_source: SchemaSource = source
        registry = (
            ExtensionRegistry()
            .with_trace_adapter("custom", trace_adapter)
            .with_schema_source("custom", schema_source)
        )

        self.assertEqual(("custom",), registry.trace_adapter_names)
        self.assertEqual(("custom",), registry.schema_source_names)
        self.assertTrue(importlib.resources.files("agentcompat").joinpath("py.typed").is_file())

    def test_rejects_unknown_and_invalid_extension_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "custom.jsonl"
            path.write_text('{"event":"tool"}\n', encoding="utf-8")

            with self.assertRaisesRegex(InputError, "Unsupported trace format"):
                read_traces(path, trace_format="missing")

            registry = ExtensionRegistry().with_trace_adapter(
                "bad",
                cast(TraceAdapter, lambda _payload, _line: (42,)),
            )
            with self.assertRaisesRegex(InputError, "non-ToolCall"):
                read_traces(path, trace_format="bad", extensions=registry)

            with self.assertRaisesRegex(InputError, "Unsupported schema source"):
                load_tool_bundle(path, schema_source="missing")

            invalid_registry = ExtensionRegistry().with_schema_source(
                "bad",
                cast(SchemaSource, lambda _path: {"search": True}),
            )
            with self.assertRaisesRegex(InputError, "invalid schema"):
                load_tool_bundle(path, schema_source="bad", extensions=invalid_registry)

    def test_rejects_invalid_extension_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-empty"):
            ExtensionRegistry().with_trace_adapter("", lambda _payload, _line: ())
        with self.assertRaisesRegex(ValueError, "whitespace"):
            ExtensionRegistry().with_schema_source(
                "bad name",
                lambda _path: {"search": {"type": "object"}},
            )


if __name__ == "__main__":
    unittest.main()
