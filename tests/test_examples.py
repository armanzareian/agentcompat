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
from agentcompat.github_action import main as action_main


class ExampleWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).parents[1]

    def test_order_api_example_has_expected_compatibility_score(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "check",
                    "--baseline",
                    str(self.root / "examples/order-api/baseline.json"),
                    "--candidate",
                    str(self.root / "examples/order-api/candidate.json"),
                    "--traces",
                    str(self.root / "examples/order-api/traces.jsonl"),
                    "--format",
                    "json",
                    "--fail-under",
                    "50",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual(53.85, payload["summary"]["score"])
        self.assertEqual(4, payload["summary"]["broken"])
        self.assertEqual(1, payload["summary"]["excluded"])

    def test_order_api_openai_example_matches_canonical_score(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "check",
                    "--baseline",
                    str(self.root / "examples/order-api/baseline.json"),
                    "--candidate",
                    str(self.root / "examples/order-api/candidate.json"),
                    "--traces",
                    str(self.root / "examples/order-api/openai-traces.jsonl"),
                    "--trace-format",
                    "openai",
                    "--redact-path",
                    "$.customer_id",
                    "--format",
                    "json",
                    "--fail-under",
                    "50",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual(53.85, payload["summary"]["score"])
        self.assertEqual(4, payload["summary"]["broken"])
        self.assertEqual(1, payload["summary"]["excluded"])

    def test_labeled_example_suite_reaches_perfect_detection(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "eval",
                    "--suite",
                    str(self.root / "examples/order-api/suite.json"),
                    "--format",
                    "json",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual(1.0, payload["aggregate"]["precision"])
        self.assertEqual(1.0, payload["aggregate"]["recall"])
        self.assertEqual(1.0, payload["aggregate"]["root_cause_accuracy"])

    def test_github_action_fixture_policies_cover_expected_outcomes(self) -> None:
        policies = (
            ("pass-policy.json", 0),
            ("fail-policy.json", 1),
            ("malformed-policy.json", 2),
        )
        for policy_name, expected_exit in policies:
            with self.subTest(policy=policy_name), tempfile.TemporaryDirectory() as directory:
                output_dir = Path(directory)
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    exit_code = action_main(
                        [
                            "--config",
                            str(self.root / "examples/github-action" / policy_name),
                            "--report-json",
                            str(output_dir / "report.json"),
                            "--sarif",
                            str(output_dir / "report.sarif"),
                        ],
                        cwd=self.root,
                    )

                self.assertEqual(expected_exit, exit_code)
                self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
