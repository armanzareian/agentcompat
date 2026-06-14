from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.cli import main


class CheckCommandTests(unittest.TestCase):
    def test_check_outputs_json_and_enforces_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            traces = root / "traces.jsonl"
            baseline.write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "search",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"query": {"type": "string"}},
                                    "required": ["query"],
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            candidate.write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "search",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string"},
                                        "tenant_id": {"type": "string"},
                                    },
                                    "required": ["query", "tenant_id"],
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            traces.write_text(
                '{"trace_id":"trace-1","tool":"search","arguments":{"query":"x"}}\n',
                encoding="utf-8",
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "check",
                        "--baseline",
                        str(baseline),
                        "--candidate",
                        str(candidate),
                        "--traces",
                        str(traces),
                        "--format",
                        "json",
                        "--fail-under",
                        "50",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertEqual(0.0, payload["summary"]["score"])
        self.assertEqual("missing_required", payload["results"][0]["issues"][0]["code"])

    def test_audit_reports_unsupported_schema_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle.json"
            bundle.write_text(
                json.dumps(
                    {
                        "tools": [
                            {
                                "name": "search",
                                "inputSchema": {
                                    "type": "array",
                                    "contains": {"type": "string"},
                                    "minContains": 1,
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "audit",
                        "--schema",
                        str(bundle),
                        "--format",
                        "json",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertFalse(payload["supported"])
        self.assertEqual(
            ["contains", "minContains"],
            sorted(issue["keyword"] for issue in payload["unsupported"]),
        )

    def test_audit_text_confirms_supported_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "bundle.json"
            bundle.write_text(
                '{"tools":[{"name":"search","inputSchema":{"type":"object"}}]}',
                encoding="utf-8",
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                exit_code = main(["audit", "--schema", str(bundle)])

        self.assertEqual(0, exit_code)
        self.assertIn("all encountered", output.getvalue().lower())

    def test_check_rejects_unsupported_semantics_before_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.json"
            candidate = root / "candidate.json"
            traces = root / "traces.jsonl"
            baseline.write_text(
                '{"tools":[{"name":"search","inputSchema":{"type":"object"}}]}',
                encoding="utf-8",
            )
            candidate.write_text(
                (
                    '{"tools":[{"name":"search","inputSchema":'
                    '{"type":"object","patternProperties":{"^x-":{"type":"string"}}}}]}'
                ),
                encoding="utf-8",
            )
            traces.write_text(
                '{"trace_id":"trace-1","tool":"search","arguments":{}}\n',
                encoding="utf-8",
            )
            error = io.StringIO()

            with contextlib.redirect_stderr(error):
                exit_code = main(
                    [
                        "check",
                        "--baseline",
                        str(baseline),
                        "--candidate",
                        str(candidate),
                        "--traces",
                        str(traces),
                    ]
                )

        self.assertEqual(2, exit_code)
        self.assertIn("patternProperties", error.getvalue())


if __name__ == "__main__":
    unittest.main()
