# Contributing

Contributions that improve compatibility analysis, input adapters, diagnostics, or evaluation
quality are welcome.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

Run the checks before opening a pull request:

```bash
ruff check .
ruff format --check .
mypy
pytest --cov
```

The dependency-free checks are also available:

```bash
make test
make quality
```

## Change expectations

- Add or update tests for behavior changes.
- Keep public types and JSON output backward compatible unless the change is documented.
- Use synthetic or anonymized traces only.
- Do not commit credentials, private instructions, production payloads, or customer identifiers.
- State measurable results as fixture or benchmark results, not universal performance claims.

## Pull requests

Keep each pull request focused. Describe the problem, design choice, validation performed, and
any compatibility or security implications. New schema keywords should include positive,
negative, nested, and malformed-schema tests.
