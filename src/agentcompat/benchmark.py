from __future__ import annotations

import time
import tracemalloc
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, cast

from agentcompat.analyzer import analyze_compatibility
from agentcompat.models import ToolCall
from agentcompat.validator import audit_tool_bundle, validate_instance

SCENARIO_NAME = "synthetic-tool-schema-evolution"


@dataclass(frozen=True, slots=True)
class BenchmarkCounts:
    score: float
    passed: int
    broken: int
    excluded: int
    eligible_weight: float
    passing_weight: float


def run_synthetic_benchmark(
    *,
    call_count: int,
    sample_size: int,
    sample_seed: int,
    score_tolerance: float,
    max_memory_mib: float,
) -> dict[str, object]:
    baseline, candidate = _synthetic_bundles()
    _audit_synthetic_bundle(baseline, candidate)

    tracemalloc.start()
    started = time.perf_counter()
    try:
        exact = _score_streaming(
            baseline,
            candidate,
            _synthetic_traces(call_count),
        )
        sampled = analyze_compatibility(
            baseline,
            candidate,
            _synthetic_traces(call_count),
            sample_size=sample_size,
            sample_seed=sample_seed,
        )
        elapsed_seconds = time.perf_counter() - started
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    score_delta = round(abs(exact.score - sampled.score), 2)
    peak_mib = peak_bytes / (1024 * 1024)
    within_tolerance = score_delta <= score_tolerance
    within_ceiling = peak_mib <= max_memory_mib
    return {
        "scenario": SCENARIO_NAME,
        "calls": call_count,
        "elapsed_seconds": round(elapsed_seconds, 4),
        "exact": _counts_to_dict(exact),
        "sampled": {
            "score": sampled.score,
            "passed": sampled.passed,
            "broken": sampled.broken,
            "excluded": sampled.excluded,
            "eligible_weight": sampled.eligible_weight,
            "passing_weight": sampled.passing_weight,
            "requested_size": sample_size,
            "seed": sample_seed,
            "sampled": sampled.sampling.sampled if sampled.sampling is not None else 0,
            "population": sampled.sampling.population if sampled.sampling is not None else 0,
        },
        "agreement": {
            "score_delta": score_delta,
            "tolerance": score_tolerance,
            "within_tolerance": within_tolerance,
        },
        "memory": {
            "peak_traced_mib": round(peak_mib, 3),
            "ceiling_mib": max_memory_mib,
            "within_ceiling": within_ceiling,
        },
        "passed_policy": within_tolerance and within_ceiling,
    }


def render_benchmark_text(payload: dict[str, object]) -> str:
    exact = _object_map(payload["exact"])
    sampled = _object_map(payload["sampled"])
    agreement = _object_map(payload["agreement"])
    memory = _object_map(payload["memory"])
    lines = [
        "AgentCompat synthetic benchmark",
        f"Scenario: {payload['scenario']}",
        f"Calls: {payload['calls']}",
        f"Exact score: {_number(exact['score']):.2f}/100",
        (
            "Sampled score: "
            f"{_number(sampled['score']):.2f}/100 "
            f"({sampled['sampled']}/{sampled['population']} calls, seed {sampled['seed']})"
        ),
        (
            "Score agreement: "
            f"delta {_number(agreement['score_delta']):.2f} "
            f"<= tolerance {_number(agreement['tolerance']):.2f}"
        ),
        (
            "Peak traced memory: "
            f"{_number(memory['peak_traced_mib']):.3f} MiB "
            f"<= ceiling {_number(memory['ceiling_mib']):.3f} MiB"
        ),
    ]
    return "\n".join(lines)


def _score_streaming(
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
    traces: Iterable[ToolCall],
) -> BenchmarkCounts:
    passed = 0
    broken = 0
    excluded = 0
    eligible_weight = 0.0
    passing_weight = 0.0

    for trace in traces:
        baseline_schema = baseline.get(trace.tool)
        if baseline_schema is None:
            excluded += 1
            continue
        if validate_instance(trace.arguments, baseline_schema):
            excluded += 1
            continue

        eligible_weight += trace.weight
        candidate_schema = candidate.get(trace.tool)
        if candidate_schema is None or validate_instance(trace.arguments, candidate_schema):
            broken += 1
        else:
            passed += 1
            passing_weight += trace.weight

    score = round((passing_weight / eligible_weight) * 100, 2) if eligible_weight else 0.0
    return BenchmarkCounts(
        score=score,
        passed=passed,
        broken=broken,
        excluded=excluded,
        eligible_weight=eligible_weight,
        passing_weight=passing_weight,
    )


def _synthetic_bundles() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    baseline = {
        "search_orders": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "closed", "fulfilled"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["customer_id", "status", "limit"],
            "additionalProperties": False,
        },
        "lookup_customer": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "tier": {"type": "string", "enum": ["standard", "legacy"]},
            },
            "required": ["customer_id", "tier"],
            "additionalProperties": False,
        },
        "create_refund": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number", "minimum": 1, "maximum": 1000},
                "reason": {"type": "string"},
            },
            "required": ["order_id", "amount", "reason"],
            "additionalProperties": False,
        },
    }
    candidate = {
        "search_orders": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "status": {"type": "string", "enum": ["open", "fulfilled"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["customer_id", "status", "limit"],
            "additionalProperties": False,
        },
        "lookup_customer": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "tier": {"type": "string", "enum": ["standard"]},
            },
            "required": ["customer_id", "tier"],
            "additionalProperties": False,
        },
        "create_refund": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "amount": {"type": "number", "minimum": 1, "maximum": 500},
                "reason": {"type": "string"},
            },
            "required": ["order_id", "amount", "reason"],
            "additionalProperties": False,
        },
    }
    return baseline, candidate


def _synthetic_traces(call_count: int) -> Iterator[ToolCall]:
    for index in range(call_count):
        slot = index % 10
        weight = 1.0 + ((index % 5) * 0.25)
        if slot < 6:
            status = "closed" if index % 9 == 0 else ("fulfilled" if index % 3 == 0 else "open")
            limit = 75 if index % 11 == 0 else 25
            yield ToolCall(
                trace_id=f"synthetic-search-{index}",
                tool="search_orders",
                arguments={
                    "customer_id": f"customer-{index % 1000}",
                    "status": status,
                    "limit": limit,
                },
                weight=weight,
            )
        elif slot < 9:
            tier = "legacy" if index % 13 == 0 else "standard"
            yield ToolCall(
                trace_id=f"synthetic-lookup-{index}",
                tool="lookup_customer",
                arguments={
                    "customer_id": f"customer-{index % 1000}",
                    "tier": tier,
                },
                weight=weight,
            )
        else:
            amount = 700 if index % 7 == 0 else 120
            yield ToolCall(
                trace_id=f"synthetic-refund-{index}",
                tool="create_refund",
                arguments={
                    "order_id": f"order-{index}",
                    "amount": amount,
                    "reason": "customer_request",
                },
                weight=weight,
            )


def _audit_synthetic_bundle(
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
) -> None:
    unsupported = audit_tool_bundle(baseline) + audit_tool_bundle(candidate)
    if unsupported:
        keywords = ", ".join(issue.keyword for issue in unsupported)
        raise AssertionError(f"Synthetic benchmark uses unsupported schema keywords: {keywords}.")


def _counts_to_dict(counts: BenchmarkCounts) -> dict[str, float | int]:
    return {
        "score": counts.score,
        "passed": counts.passed,
        "broken": counts.broken,
        "excluded": counts.excluded,
        "eligible_weight": counts.eligible_weight,
        "passing_weight": counts.passing_weight,
    }


def _object_map(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("Expected benchmark payload field to be an object.")
    return value


def _number(value: object) -> float:
    return float(cast(float | int, value))
