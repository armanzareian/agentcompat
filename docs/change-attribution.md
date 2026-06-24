# Change Attribution

AgentCompat combines structural schema comparison with trace replay so a candidate failure can
name the schema change that caused it. Attribution is deterministic and does not use a model or
network service.

## Change inventory

The `changes` JSON array contains breaking or potentially breaking structural changes detected
between normalized tool bundles:

| Kind | Meaning |
| --- | --- |
| `tool_removed` | A baseline tool is absent from the candidate bundle. |
| `required_added` | A property became required. |
| `property_removed` | A baseline property is absent from candidate `properties`. |
| `type_changed` | The candidate no longer accepts every baseline JSON type. |
| `enum_narrowed` | At least one baseline enum value is no longer accepted. |
| `constraint_tightened` | A bound, pattern, format, constant, uniqueness rule, false schema, or `additionalProperties` policy became more restrictive. |

Each record includes `tool`, instance `path`, changed `keyword`, `before`, `after`, and a
`change_id`. The ID is the `chg_` prefix plus a truncated SHA-256 digest of the canonical change
record. JSON object key order does not affect it. Changing the tool, path, kind, keyword, or
before/after values produces a different ID.

Only aligned schema locations are compared. AgentCompat does not claim semantic equivalence for
arbitrary `allOf`, `anyOf`, or `oneOf` rewrites.

## Tool risk summaries

The `tools` JSON array summarizes observed compatibility by tool. Each item includes the tool
name, score, passed/broken/excluded counts, eligible weight, passing weight, `risk_weight`
(eligible weight that failed under the candidate), and `excluded_weight` (baseline-invalid
weight omitted from the score).

Items are sorted by highest `risk_weight`, then broken call count, excluded weight, and tool
name. This keeps the most compatibility-sensitive tools visible even when the full result list
is long.

## Failure links

Every result issue contains a `change_ids` array. Candidate failures are linked using:

- tool name;
- normalized instance path, with concrete array indexes converted to `[*]`;
- validation issue class, such as `missing_required` or `enum_mismatch`;
- schema keyword for scalar constraints.

An issue can have no link when it is baseline-invalid, when the structural cause is outside the
supported comparison model, or when multiple schema rewrites prevent an exact match. An issue
can link to multiple IDs when multiple aligned changes explain the same failure.

## Migration ranking

The `migration_plan` array contains only changes that broke at least one baseline-valid trace.
Repeated issues are deduplicated by change and trace before weight is summed.

Items are sorted by:

1. affected trace weight, descending;
2. affected trace count, descending;
3. tool, path, change kind, and change ID.

Each item includes its one-based `rank`, affected `trace_ids`, `affected_weight`, `issue_count`,
and deterministic guidance. Ranking measures observed migration impact, not implementation
effort or business priority beyond the weights supplied in the trace file.

## JSON example

```json
{
  "tools": [
    {
      "tool": "search_orders",
      "score": 0.0,
      "passed": 0,
      "broken": 1,
      "excluded": 0,
      "eligible_weight": 2.0,
      "passing_weight": 0.0,
      "risk_weight": 2.0,
      "excluded_weight": 0.0
    }
  ],
  "changes": [
    {
      "change_id": "chg_d7a493cd0fb45aeb",
      "kind": "required_added",
      "tool": "search_orders",
      "path": "$.customer_id",
      "keyword": "required",
      "before": false,
      "after": true
    }
  ],
  "results": [
    {
      "trace_id": "break-required",
      "issues": [
        {
          "code": "missing_required",
          "path": "$.customer_id",
          "change_ids": ["chg_d7a493cd0fb45aeb"]
        }
      ]
    }
  ],
  "migration_plan": [
    {
      "rank": 1,
      "change_id": "chg_d7a493cd0fb45aeb",
      "kind": "required_added",
      "affected_weight": 2.0,
      "affected_traces": 1,
      "trace_ids": ["break-required"]
    }
  ]
}
```
