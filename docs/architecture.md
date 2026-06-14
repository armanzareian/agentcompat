# Architecture

## Objective

AgentCompat answers one release question: given tool calls observed under a baseline schema,
which calls remain valid under a candidate schema, and why do the others fail?

The v0.1 boundary is deliberately offline and deterministic. It does not call an LLM, invoke a
tool, mutate a payload, or upload traces.

## Components

### Input normalization

`agentcompat.io` loads bounded local files and converts MCP-style `inputSchema` and OpenAI-style
`function.parameters` definitions into a map from tool name to JSON Schema. It resolves local
JSON Pointer references while enforcing a bundle-directory sandbox and rejecting reference
cycles. Trace records use a small canonical contract: `trace_id`, `tool`, object-valued
`arguments`, and a positive `weight`.

### Schema validation

`agentcompat.validator` implements the common assertion subset used by function tools. Every
issue has a stable code, JSON path, human-readable message, expected value, and actual value.
Annotation keywords and provider extensions do not change validation.

The current subset covers:

- `type`, including unions
- `properties`, `required`, and `additionalProperties`
- `enum` and `const`
- `minimum`, `maximum`, `exclusiveMinimum`, and `exclusiveMaximum`
- `multipleOf`
- `minLength`, `maxLength`, `minItems`, `maxItems`, `minProperties`, and `maxProperties`
- `pattern` and common standard-library-backed `format` checks
- homogeneous `items`, `prefixItems`, legacy tuple arrays, and `uniqueItems`
- `allOf`, `anyOf`, and `oneOf`
- `if`, `then`, and `else`
- sandboxed local `$ref` values and boolean schemas

Full Draft 2020-12 coverage is not implied. The validator audits the complete schema before
replay and rejects unsupported keywords instead of silently skipping their semantics. The
executable inventory is documented in [JSON Schema support](schema-support.md).

### Replay analysis

`agentcompat.analyzer` validates each trace against the baseline first. Unknown tools and
baseline-invalid calls are excluded because they are not evidence of a candidate regression.
Eligible calls are then validated against the candidate.

The compatibility score is:

```text
100 * sum(weight of candidate-valid calls) / sum(weight of baseline-valid calls)
```

Results retain trace order and distinguish `passed`, `broken`, and `excluded` states.

### Evaluation

`agentcompat.evaluation` compares predicted broken trace IDs and issue codes with labeled
expectations. It reports trace-level precision, recall, and F1, plus root-cause accuracy.
Evaluation manifests resolve fixture paths relative to the manifest file for portable suites.

### Presentation

`agentcompat.report` is a pure rendering layer. JSON output uses stable field names for CI, while
text output prioritizes the score, evidence denominator, breakage path, and repair hint.

## Failure model

Malformed JSON, duplicate tools, invalid trace shapes, non-positive weights, oversized files,
trace-count overflow, unsafe references, reference cycles, and unsupported schema semantics are
input errors. They return CLI exit code `2`. A compatibility score below policy returns `1`. A
completed check at or above policy returns `0`. The standalone `audit` command returns `1` when
unsupported semantics are present.

## Security posture

- Offline by default; no network client is present.
- No dynamic imports, expression evaluation, shell execution, or tool invocation.
- Input files are size-bounded and traces are count-bounded.
- Reference files are restricted to the tool bundle directory and are size-bounded.
- Trace arguments are never written to a cache or log by the library.
- CI permissions are read-only.

The repository does not yet provide automated secret detection or field-level redaction. Those
controls are part of the trace-adapter milestone before production telemetry ingestion.
