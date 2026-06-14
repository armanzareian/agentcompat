from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from agentcompat.validator import (
    ANNOTATION_KEYWORDS,
    SUPPORTED_FORMATS,
    SUPPORTED_KEYWORDS,
)

SUPPORT_DOC = Path(__file__).parents[1] / "docs" / "schema-support.md"


class SchemaSupportDocumentationTests(unittest.TestCase):
    def test_support_matrix_covers_executable_keyword_inventory(self) -> None:
        documentation = SUPPORT_DOC.read_text(encoding="utf-8")

        for keyword in sorted(SUPPORTED_KEYWORDS | ANNOTATION_KEYWORDS):
            with self.subTest(keyword=keyword):
                self.assertIn(f"`{keyword}`", documentation)
        for format_name in sorted(SUPPORTED_FORMATS):
            with self.subTest(format=format_name):
                self.assertIn(f"`{format_name}`", documentation)


if __name__ == "__main__":
    unittest.main()
