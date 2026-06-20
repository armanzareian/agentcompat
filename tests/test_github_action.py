from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.github_action import main


class GitHubActionTests(unittest.TestCase):
    def test_action_writes_summary_report_outputs_and_sarif_for_policy_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, candidate, traces = self._write_fixture(root)
            summary = root / "summary.md"
            outputs = root / "outputs.txt"
            report = root / "agentcompat-report.json"
            sarif = root / "agentcompat.sarif"
            config = root / ".agentcompat.json"
            config.write_text(
                json.dumps(
                    {
                        "baseline": baseline.name,
                        "traces": traces.name,
                        "trace_format": "canonical",
                        "fail_under": 80,
                        "changed_schema_discovery": {
                            "enabled": True,
                            "globs": ["schemas/*.json"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "GITHUB_OUTPUT": str(outputs),
                "GITHUB_STEP_SUMMARY": str(summary),
            }

            exit_code = main(
                [
                    "--config",
                    str(config),
                    "--report-json",
                    str(report),
                    "--sarif",
                    str(sarif),
                ],
                env=env,
                cwd=root,
            )

            self.assertEqual(1, exit_code)
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(0.0, payload["summary"]["score"])
            self.assertEqual(1, payload["summary"]["broken"])
            summary_text = summary.read_text(encoding="utf-8")
            self.assertIn("AgentCompat compatibility", summary_text)
            self.assertIn("Score: 0.00/100", summary_text)
            self.assertIn("[trace-1](#trace-trace-1)", summary_text)
            output_text = outputs.read_text(encoding="utf-8")
            self.assertIn("score=0.00", output_text)
            self.assertIn("broken=1", output_text)
            sarif_payload = json.loads(sarif.read_text(encoding="utf-8"))
            self.assertEqual("2.1.0", sarif_payload["version"])
            result = sarif_payload["runs"][0]["results"][0]
            location = result["locations"][0]["physicalLocation"]["artifactLocation"]
            self.assertEqual("missing_required", result["ruleId"])
            self.assertEqual("trace-1", result["properties"]["trace_id"])
            self.assertEqual("schemas/candidate.json", location["uri"])

    def test_action_returns_success_when_score_meets_policy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, candidate, traces = self._write_fixture(root, include_tenant=True)
            summary = root / "summary.md"
            report = root / "report.json"
            sarif = root / "report.sarif"
            env = {"GITHUB_STEP_SUMMARY": str(summary)}

            exit_code = main(
                [
                    "--baseline",
                    str(baseline),
                    "--candidate",
                    str(candidate),
                    "--traces",
                    str(traces),
                    "--fail-under",
                    "100",
                    "--report-json",
                    str(report),
                    "--sarif",
                    str(sarif),
                ],
                env=env,
                cwd=root,
            )

            self.assertEqual(0, exit_code)
            self.assertIn("Score: 100.00/100", summary.read_text(encoding="utf-8"))
            sarif_payload = json.loads(sarif.read_text(encoding="utf-8"))
            self.assertEqual([], sarif_payload["runs"][0]["results"])

    def test_action_reports_malformed_input_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, candidate, traces = self._write_fixture(root)
            traces.write_text("{not json}\n", encoding="utf-8")
            summary = root / "summary.md"
            error = io.StringIO()

            with contextlib.redirect_stderr(error):
                exit_code = main(
                    [
                        "--baseline",
                        str(baseline),
                        "--candidate",
                        str(candidate),
                        "--traces",
                        str(traces),
                        "--report-json",
                        str(root / "report.json"),
                        "--sarif",
                        str(root / "report.sarif"),
                    ],
                    env={"GITHUB_STEP_SUMMARY": str(summary)},
                    cwd=root,
                )

            self.assertEqual(2, exit_code)
            self.assertIn("Invalid JSON on line 1", error.getvalue())
            self.assertNotIn("Traceback", error.getvalue())
            self.assertIn("Input error", summary.read_text(encoding="utf-8"))

    def _write_fixture(
        self,
        root: Path,
        *,
        include_tenant: bool = False,
    ) -> tuple[Path, Path, Path]:
        schema_dir = root / "schemas"
        schema_dir.mkdir()
        baseline = root / "baseline.json"
        candidate = schema_dir / "candidate.json"
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
        arguments = {"query": "orders"}
        if include_tenant:
            arguments["tenant_id"] = "tenant-a"
        traces.write_text(
            json.dumps(
                {
                    "trace_id": "trace-1",
                    "tool": "search",
                    "arguments": arguments,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return baseline, candidate, traces


if __name__ == "__main__":
    unittest.main()
