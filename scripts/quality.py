from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
MAX_LINE_LENGTH = 100


def main() -> int:
    failures: list[str] = []
    for path in sorted((*ROOT.glob("src/**/*.py"), *ROOT.glob("tests/**/*.py"))):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        try:
            ast.parse(text, filename=str(relative))
        except SyntaxError as exc:
            failures.append(f"{relative}:{exc.lineno}: syntax error: {exc.msg}")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if line.rstrip() != line:
                failures.append(f"{relative}:{line_number}: trailing whitespace")
            if "\t" in line:
                failures.append(f"{relative}:{line_number}: tab character")
            if len(line) > MAX_LINE_LENGTH:
                failures.append(
                    f"{relative}:{line_number}: line exceeds {MAX_LINE_LENGTH} characters"
                )

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("quality checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
