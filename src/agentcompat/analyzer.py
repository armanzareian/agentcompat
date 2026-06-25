from __future__ import annotations

import hashlib
import heapq
import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from agentcompat.changes import (
    attribute_issues,
    build_migration_plan,
    compare_tool_bundles,
)
from agentcompat.models import (
    CompatibilityReport,
    SamplingStratum,
    SamplingSummary,
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
    *,
    sample_size: int | None = None,
    sample_seed: int = 0,
) -> CompatibilityReport:
    unsupported = audit_tool_bundle(baseline) + audit_tool_bundle(candidate)
    if unsupported:
        raise UnsupportedSchemaError(unsupported)

    changes = compare_tool_bundles(baseline, candidate)
    sampling: SamplingSummary | None = None
    if sample_size is not None:
        traces, sampling = _sample_traces(traces, sample_size, sample_seed)

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
        sampling,
    )


@dataclass(slots=True)
class _ReservoirItem:
    priority: float
    index: int
    trace: ToolCall


@dataclass(slots=True)
class _StratumSample:
    population: int = 0
    population_weight: float = 0.0
    heap: list[tuple[float, int, _ReservoirItem]] = field(default_factory=list)


@dataclass(slots=True)
class _ToolStats:
    passed: int = 0
    broken: int = 0
    excluded: int = 0
    eligible_weight: float = 0.0
    passing_weight: float = 0.0
    excluded_weight: float = 0.0


def _sample_traces(
    traces: Iterable[ToolCall],
    sample_size: int,
    sample_seed: int,
) -> tuple[tuple[ToolCall, ...], SamplingSummary]:
    if sample_size <= 0:
        raise ValueError("sample_size must be positive.")

    strata: dict[str, _StratumSample] = {}
    for index, trace in enumerate(traces):
        stratum = strata.setdefault(trace.tool, _StratumSample())
        stratum.population += 1
        stratum.population_weight += trace.weight
        priority = _sampling_priority(trace, sample_seed, index)
        item = _ReservoirItem(priority, index, trace)
        heap_item = (-priority, -index, item)
        if len(stratum.heap) < sample_size:
            heapq.heappush(stratum.heap, heap_item)
        elif heap_item > stratum.heap[0]:
            heapq.heapreplace(stratum.heap, heap_item)

    quotas = _sample_quotas(strata, sample_size)
    selected: list[_ReservoirItem] = []
    for tool, quota in quotas.items():
        if quota <= 0:
            continue
        candidates = sorted(
            (heap_item[2] for heap_item in strata[tool].heap),
            key=lambda item: (item.priority, item.index),
        )
        selected.extend(candidates[:quota])

    selected.sort(key=lambda item: item.index)
    sampled_traces = tuple(item.trace for item in selected)
    sampled_by_tool: dict[str, tuple[int, float]] = {}
    for item in selected:
        count, weight = sampled_by_tool.get(item.trace.tool, (0, 0.0))
        sampled_by_tool[item.trace.tool] = (count + 1, weight + item.trace.weight)

    population = sum(stratum.population for stratum in strata.values())
    population_weight = sum(stratum.population_weight for stratum in strata.values())
    sampled_weight = sum(trace.weight for trace in sampled_traces)
    summary = SamplingSummary(
        requested_size=sample_size,
        seed=sample_seed,
        population=population,
        sampled=len(sampled_traces),
        population_weight=population_weight,
        sampled_weight=sampled_weight,
        strata=tuple(
            SamplingStratum(
                tool=tool,
                population=stratum.population,
                sampled=sampled_by_tool.get(tool, (0, 0.0))[0],
                population_weight=stratum.population_weight,
                sampled_weight=sampled_by_tool.get(tool, (0, 0.0))[1],
            )
            for tool, stratum in sorted(strata.items())
        ),
    )
    return sampled_traces, summary


def _sampling_priority(trace: ToolCall, seed: int, index: int) -> float:
    digest = hashlib.sha256(f"{seed}\0{index}\0{trace.tool}\0{trace.trace_id}".encode()).digest()
    random_value = (int.from_bytes(digest[:8], "big") + 1) / ((1 << 64) + 1)
    return -math.log(random_value) / trace.weight


def _sample_quotas(strata: dict[str, _StratumSample], sample_size: int) -> dict[str, int]:
    population = sum(stratum.population for stratum in strata.values())
    target = min(sample_size, population)
    quotas = {tool: 0 for tool in strata}
    if target <= 0:
        return quotas

    nonempty = [tool for tool, stratum in strata.items() if stratum.population > 0]
    if target >= len(nonempty):
        for tool in nonempty:
            quotas[tool] = 1

    remaining = target - sum(quotas.values())
    _allocate_remaining_quota(strata, quotas, remaining)
    return quotas


def _allocate_remaining_quota(
    strata: dict[str, _StratumSample],
    quotas: dict[str, int],
    remaining: int,
) -> None:
    while remaining > 0:
        allocatable = [
            (tool, stratum) for tool, stratum in strata.items() if quotas[tool] < stratum.population
        ]
        if not allocatable:
            return
        allocatable_weight = sum(stratum.population_weight for _, stratum in allocatable)
        allocations: list[tuple[float, float, int, str, int]] = []
        for tool, stratum in allocatable:
            raw = (
                remaining * (stratum.population_weight / allocatable_weight)
                if allocatable_weight
                else 0.0
            )
            extra = min(math.floor(raw), stratum.population - quotas[tool])
            if extra:
                quotas[tool] += extra
                remaining -= extra
            allocations.append(
                (
                    raw - math.floor(raw),
                    stratum.population_weight,
                    stratum.population,
                    tool,
                    stratum.population - quotas[tool],
                )
            )
        if remaining <= 0:
            return
        allocated = False
        for _, _, _, tool, capacity in sorted(allocations, reverse=True):
            if capacity <= 0:
                continue
            quotas[tool] += 1
            remaining -= 1
            allocated = True
            if remaining <= 0:
                return
        if not allocated:
            return


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
