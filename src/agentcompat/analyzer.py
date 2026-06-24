from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agentcompat.changes import (
    attribute_issues,
    build_migration_plan,
    compare_tool_bundles,
)
from agentcompat.models import (
    CompatibilityReport,
    ToolCall,
    ToolSummary,
    TraceResult,
    ValidationIssue,
)
from agentcompat.validator import (
    UnsupportedSchemaError,
    audit_tool_bundle,
    validate_instance,
)


def analyze_compatibility(
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
    traces: Iterable[ToolCall],
) -> CompatibilityReport:
    unsupported = audit_tool_bundle(baseline) + audit_tool_bundle(candidate)
    if unsupported:
        raise UnsupportedSchemaError(unsupported)

    changes = compare_tool_bundles(baseline, candidate)
    results: list[TraceResult] = []
    eligible_weight = 0.0
    passing_weight = 0.0
    passed = 0
    broken = 0
    excluded = 0
    tool_stats: dict[str, _ToolStats] = {}

    for trace in traces:
        stats = tool_stats.setdefault(trace.tool, _ToolStats())
        baseline_schema = baseline.get(trace.tool)
        if baseline_schema is None:
            excluded += 1
            stats.excluded += 1
            stats.excluded_weight += trace.weight
            results.append(
                TraceResult(
                    trace,
                    "excluded",
                    (
                        ValidationIssue(
                            "baseline_unknown_tool",
                            "$",
                            f"Tool {trace.tool!r} is absent from the baseline bundle.",
                        ),
                    ),
                )
            )
            continue

        baseline_issues = validate_instance(trace.arguments, baseline_schema)
        if baseline_issues:
            excluded += 1
            stats.excluded += 1
            stats.excluded_weight += trace.weight
            results.append(TraceResult(trace, "excluded", tuple(baseline_issues)))
            continue

        eligible_weight += trace.weight
        stats.eligible_weight += trace.weight
        candidate_schema = candidate.get(trace.tool)
        issues: tuple[ValidationIssue, ...]
        if candidate_schema is None:
            issues = (
                ValidationIssue(
                    "tool_removed",
                    "$",
                    f"Tool {trace.tool!r} is absent from the candidate bundle.",
                ),
            )
        else:
            issues = tuple(validate_instance(trace.arguments, candidate_schema))
        issues = attribute_issues(trace.tool, issues, changes)

        if issues:
            broken += 1
            stats.broken += 1
            results.append(
                TraceResult(
                    trace,
                    "broken",
                    issues,
                    tuple(_repair_hint(issue) for issue in issues),
                )
            )
        else:
            passed += 1
            stats.passed += 1
            passing_weight += trace.weight
            stats.passing_weight += trace.weight
            results.append(TraceResult(trace, "passed"))

    score = 0.0
    if eligible_weight:
        score = round((passing_weight / eligible_weight) * 100, 2)

    result_tuple = tuple(results)
    return CompatibilityReport(
        score,
        passed,
        broken,
        excluded,
        eligible_weight,
        passing_weight,
        result_tuple,
        changes,
        build_migration_plan(changes, result_tuple),
        _tool_summaries(tool_stats),
    )


@dataclass(slots=True)
class _ToolStats:
    passed: int = 0
    broken: int = 0
    excluded: int = 0
    eligible_weight: float = 0.0
    passing_weight: float = 0.0
    excluded_weight: float = 0.0


def _tool_summaries(tool_stats: dict[str, _ToolStats]) -> tuple[ToolSummary, ...]:
    summaries = []
    for tool, stats in tool_stats.items():
        score = 0.0
        if stats.eligible_weight:
            score = round((stats.passing_weight / stats.eligible_weight) * 100, 2)
        summaries.append(
            ToolSummary(
                tool=tool,
                score=score,
                passed=stats.passed,
                broken=stats.broken,
                excluded=stats.excluded,
                eligible_weight=stats.eligible_weight,
                passing_weight=stats.passing_weight,
                risk_weight=stats.eligible_weight - stats.passing_weight,
                excluded_weight=stats.excluded_weight,
            )
        )
    return tuple(
        sorted(
            summaries,
            key=lambda summary: (
                -summary.risk_weight,
                -summary.broken,
                -summary.excluded_weight,
                summary.tool,
            ),
        )
    )


def _repair_hint(issue: ValidationIssue) -> str:
    hints = {
        "missing_required": (
            f"Provide a default for {issue.path} during migration or keep the field optional."
        ),
        "unexpected_property": (
            f"Remove or rename {issue.path}, or allow it in additionalProperties."
        ),
        "type_mismatch": (f"Coerce {issue.path} to {issue.expected!r} before invoking the tool."),
        "enum_mismatch": (f"Map the observed value at {issue.path} to an accepted enum member."),
        "tool_removed": ("Keep a compatibility alias or migrate traces to a replacement tool."),
    }
    return hints.get(
        issue.code,
        f"Update the payload at {issue.path} to satisfy the candidate constraint.",
    )
