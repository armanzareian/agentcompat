from __future__ import annotations

from typing import Any

from agentcompat.models import CompatibilityReport, EvaluationMetrics


def report_to_dict(report: CompatibilityReport) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "summary": {
            "score": report.score,
            "passed": report.passed,
            "broken": report.broken,
            "excluded": report.excluded,
            "eligible_weight": report.eligible_weight,
            "passing_weight": report.passing_weight,
        },
        "tools": [
            {
                "tool": summary.tool,
                "score": summary.score,
                "passed": summary.passed,
                "broken": summary.broken,
                "excluded": summary.excluded,
                "eligible_weight": summary.eligible_weight,
                "passing_weight": summary.passing_weight,
                "risk_weight": summary.risk_weight,
                "excluded_weight": summary.excluded_weight,
            }
            for summary in report.tool_summaries
        ],
        "results": [
            {
                "trace_id": result.trace.trace_id,
                "tool": result.trace.tool,
                "weight": result.trace.weight,
                "status": result.status,
                "issues": [
                    {
                        "code": issue.code,
                        "path": issue.path,
                        "message": issue.message,
                        "expected": issue.expected,
                        "actual": issue.actual,
                        "change_ids": list(issue.change_ids),
                    }
                    for issue in result.issues
                ],
                "hints": list(result.hints),
            }
            for result in report.results
        ],
        "changes": [
            {
                "change_id": change.change_id,
                "kind": change.kind,
                "tool": change.tool,
                "path": change.path,
                "keyword": change.keyword,
                "before": change.before,
                "after": change.after,
                "description": change.description,
            }
            for change in report.changes
        ],
        "migration_plan": [
            {
                "rank": rank,
                "change_id": item.change_id,
                "kind": item.kind,
                "tool": item.tool,
                "path": item.path,
                "keyword": item.keyword,
                "affected_weight": item.affected_weight,
                "affected_traces": len(item.trace_ids),
                "trace_ids": list(item.trace_ids),
                "issue_count": item.issue_count,
                "guidance": item.guidance,
            }
            for rank, item in enumerate(report.migration_plan, start=1)
        ],
    }
    if report.sampling is not None:
        payload["sampling"] = {
            "requested_size": report.sampling.requested_size,
            "seed": report.sampling.seed,
            "population": report.sampling.population,
            "sampled": report.sampling.sampled,
            "population_weight": report.sampling.population_weight,
            "sampled_weight": report.sampling.sampled_weight,
            "strata": [
                {
                    "tool": stratum.tool,
                    "population": stratum.population,
                    "sampled": stratum.sampled,
                    "population_weight": stratum.population_weight,
                    "sampled_weight": stratum.sampled_weight,
                }
                for stratum in report.sampling.strata
            ],
        }
    if report.confidence_interval is not None:
        payload["confidence_interval"] = {
            "metric": report.confidence_interval.metric,
            "confidence_level": report.confidence_interval.confidence_level,
            "lower": report.confidence_interval.lower,
            "upper": report.confidence_interval.upper,
            "iterations": report.confidence_interval.iterations,
            "seed": report.confidence_interval.seed,
        }
    return payload


def render_text(report: CompatibilityReport) -> str:
    lines = [
        "AgentCompat compatibility report",
        f"Score: {report.score:.2f}/100",
        (f"Calls: {report.passed} passed, {report.broken} broken, {report.excluded} excluded"),
        (f"Observed weight: {report.passing_weight:g}/{report.eligible_weight:g} compatible"),
    ]
    if report.confidence_interval is not None:
        interval = report.confidence_interval
        lines.append(
            "Score confidence interval: "
            f"{interval.lower:.2f}-{interval.upper:.2f} "
            f"({interval.confidence_level:.0%}, "
            f"{interval.iterations} iterations, seed {interval.seed})"
        )
    if report.sampling is not None:
        lines.append(
            "Sampling: "
            f"{report.sampling.sampled}/{report.sampling.population} calls selected "
            f"with seed {report.sampling.seed}"
        )
        lines.append(
            "Sampled weight: "
            f"{report.sampling.sampled_weight:g}/{report.sampling.population_weight:g}"
        )
    if report.tool_summaries:
        lines.append("")
        lines.append("Tool risk")
        for summary in report.tool_summaries:
            lines.append(
                f"- {summary.tool}: {summary.score:.2f}/100 "
                f"(risk weight {summary.risk_weight:g}; "
                f"{summary.broken} broken, {summary.excluded} excluded)"
            )
    for result in report.results:
        if result.status == "passed":
            continue
        lines.append("")
        lines.append(f"{result.status.upper()} {result.trace.trace_id} -> {result.trace.tool}")
        for issue in result.issues:
            lines.append(f"  [{issue.code}] {issue.path}: {issue.message}")
            for change_id in issue.change_ids:
                lines.append(f"  Change: {change_id}")
        for hint in result.hints:
            lines.append(f"  Hint: {hint}")
    if report.migration_plan:
        lines.append("")
        lines.append("Migration plan")
        for rank, item in enumerate(report.migration_plan, start=1):
            trace_label = "trace" if len(item.trace_ids) == 1 else "traces"
            lines.append(
                f"{rank}. [{item.kind}] {item.tool} {item.path} "
                f"(weight {item.affected_weight:g}; {len(item.trace_ids)} {trace_label})"
            )
            lines.append(f"   Change: {item.change_id}")
            lines.append(f"   {item.guidance}")
    return "\n".join(lines)


def evaluation_to_dict(metrics: EvaluationMetrics) -> dict[str, int | float]:
    return {
        "precision": metrics.precision,
        "recall": metrics.recall,
        "f1": metrics.f1,
        "root_cause_accuracy": metrics.root_cause_accuracy,
        "true_positives": metrics.true_positives,
        "false_positives": metrics.false_positives,
        "false_negatives": metrics.false_negatives,
    }
