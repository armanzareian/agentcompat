# GitHub Action

AgentCompat ships as a composite GitHub Action for replaying observed tool-call traces in pull
request or release workflows. The action runs offline, reads local checkout files only, writes a
job summary with affected trace links, and emits JSON plus SARIF reports for downstream review.

## Workflow

```yaml
name: AgentCompat

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  compatibility:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false

      - uses: armanzareian/agentcompat@main
        with:
          config: .agentcompat.json

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: agentcompat-reports
          path: |
            agentcompat-report.json
            agentcompat.sarif
```

Use a pinned release tag or commit SHA for production workflows. The action itself does not use
the GitHub API, perform network requests, or persist checkout credentials. If you choose to
upload SARIF into GitHub code scanning, grant the permissions required by that upload step; the
AgentCompat step still only needs checked-out files.

## Policy File

The default policy file is `.agentcompat.json` when present. You can also pass a path through the
`config` input. Paths are resolved from the workflow workspace.

```json
{
  "baseline": "schemas/tools-baseline.json",
  "traces": "traces/tool-calls.jsonl",
  "trace_format": "openai",
  "fail_under": 95,
  "max_traces": 10000,
  "sample_size": 1000,
  "sample_seed": 17,
  "bootstrap_iterations": 1000,
  "confidence_level": 0.95,
  "score_tolerance": 5,
  "redact_paths": ["$.customer.email"],
  "redact_key_patterns": ["token|secret|api[_-]?key"],
  "changed_schema_discovery": {
    "enabled": true,
    "globs": ["schemas/tools-candidate.json"]
  }
}
```

Set `candidate` directly when the candidate bundle path is known. If `candidate` is omitted,
`changed_schema_discovery.globs` must match exactly one local file. This is deterministic glob
discovery, so pair it with your own checkout, generation, or changed-file step when a pull
request can touch more than one schema.

Supported policy keys:

- `baseline`: baseline tool bundle path.
- `candidate`: candidate tool bundle path.
- `traces`: trace JSONL path.
- `trace_format`: `canonical`, `openai`, `anthropic`, `mcp`, or `langchain`.
- `fail_under`: minimum score from `0` through `100`.
- `max_traces`: positive trace count cap.
- `sample_size`: optional deterministic weighted stratified sample size.
- `sample_seed`: integer seed used when `sample_size` is set.
- `bootstrap_iterations`: optional non-negative iteration count for score confidence intervals.
- `confidence_level`: bootstrap interval level greater than `0` and less than `1`.
- `score_tolerance`: optional reporting tolerance for sampled score comparisons.
- `redact_paths`: exact argument JSON paths to redact before replay.
- `redact_key_patterns`: regular expressions matching argument keys to redact.
- `changed_schema_discovery.globs`: candidate schema globs used when `candidate` is omitted.

Every policy value can be overridden by action inputs. Newline-separated `redact-paths` and
`redact-key-patterns` inputs are appended as repeated CLI flags.
Sampled runs include the selected call count, population count, seed, and sampled weight in the
job summary and JSON report. Runs with `bootstrap_iterations` also include a score confidence
interval in the job summary, JSON report, and optional confidence-bound outputs. `score_tolerance`
is reported in the summary so teams can document the acceptable difference between sampled and
exact validation checks; it does not change pass/fail policy.

## Outputs

The action writes these step outputs:

- `score`: compatibility score with two decimal places.
- `passed`: count of eligible calls that remain candidate-compatible.
- `broken`: count of eligible calls that fail under the candidate schema.
- `excluded`: count of baseline-invalid or unknown-tool calls excluded from the score.
- `score-lower`: lower bootstrap confidence bound when enabled.
- `score-upper`: upper bootstrap confidence bound when enabled.
- `report-json`: path to the machine-readable compatibility report.
- `sarif`: path to the SARIF report.

Exit code `0` means the score met policy. Exit code `1` means the score was below
`fail_under`. Exit code `2` means malformed input, unsafe references, unsupported schema
semantics, or invalid policy prevented scoring.

## Fixtures

`examples/github-action/pass-policy.json` runs the order API fixture with a passing threshold and
candidate discovery. `examples/github-action/fail-policy.json` raises the threshold to demonstrate
policy failure. `examples/github-action/malformed-policy.json` points at malformed trace input and
demonstrates exit code `2`.
