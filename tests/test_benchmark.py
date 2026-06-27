from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.cli import main


class BenchmarkCommandTests(unittest.TestCase):
    def test_synthetic_benchmark_reports_memory_and_sample_agreement(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "benchmark",
                    "--calls",
                    "240",
                    "--sample-size",
                    "120",
                    "--sample-seed",
                    "17",
                    "--score-tolerance",
                    "15",
                    "--max-memory-mib",
                    "64",
                    "--format",
                    "json",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual("synthetic-tool-schema-evolution", payload["scenario"])
        self.assertEqual(240, payload["calls"])
        self.assertEqual(240, payload["exact"]["passed"] + payload["exact"]["broken"])
        self.assertGreater(payload["exact"]["score"], 0)
        self.assertLess(payload["exact"]["score"], 100)
        self.assertEqual(120, payload["sampled"]["requested_size"])
        self.assertEqual(17, payload["sampled"]["seed"])
        self.assertLessEqual(
            payload["agreement"]["score_delta"],
            payload["agreement"]["tolerance"],
        )
        self.assertTrue(payload["agreement"]["within_tolerance"])
        self.assertLessEqual(
            payload["memory"]["peak_traced_mib"],
            payload["memory"]["ceiling_mib"],
        )
        self.assertTrue(payload["memory"]["within_ceiling"])

    def test_synthetic_benchmark_exits_nonzero_when_memory_ceiling_is_exceeded(self) -> None:
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "benchmark",
                    "--calls",
                    "20",
                    "--sample-size",
                    "10",
                    "--max-memory-mib",
                    "0.001",
                    "--format",
                    "json",
                ]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(1, exit_code)
        self.assertFalse(payload["memory"]["within_ceiling"])


if __name__ == "__main__":
    unittest.main()
