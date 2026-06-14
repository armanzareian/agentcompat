from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.validator import validate_instance

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "json-schema-subset.json"


class JsonSchemaConformanceTests(unittest.TestCase):
    def test_supported_keyword_fixtures(self) -> None:
        groups = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        assertions = 0

        for group in groups:
            schema = group["schema"]
            for instance in group["valid"]:
                assertions += 1
                with self.subTest(group=group["name"], instance=instance, valid=True):
                    self.assertEqual([], validate_instance(instance, schema))
            for instance in group["invalid"]:
                assertions += 1
                with self.subTest(group=group["name"], instance=instance, valid=False):
                    self.assertTrue(validate_instance(instance, schema))

        self.assertGreaterEqual(assertions, 50)


if __name__ == "__main__":
    unittest.main()
