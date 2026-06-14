from __future__ import annotations

from typing import Any

from agentcompat.models import CompatibilityReport, EvaluationMetrics


def report_to_dict(report: CompatibilityReport) -> dict[str, Any]:
    return {
        "summary": {
            "score": report.score,
            "passed": report.passed,
            "broken": report.broken,
            "excluded": report.excluded,
            "eligible_weight": report.eligible_weight,
            "passing_weight": report.passing_weight,
        },
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
                    }
                    for issue in result.issues
                ],
                "hints": list(result.hints),
            }
            for result in report.results
        ],
    }


def render_text(report: CompatibilityReport) -> str:
    lines = [
        "AgentCompat compatibility report",
        f"Score: {report.score:.2f}/100",
        (f"Calls: {report.passed} passed, {report.broken} broken, {report.excluded} excluded"),
        (f"Observed weight: {report.passing_weight:g}/{report.eligible_weight:g} compatible"),
    ]
    for result in report.results:
        if result.status == "passed":
            continue
        lines.append("")
        lines.append(f"{result.status.upper()} {result.trace.trace_id} -> {result.trace.tool}")
        for issue in result.issues:
            lines.append(f"  [{issue.code}] {issue.path}: {issue.message}")
        for hint in result.hints:
            lines.append(f"  Hint: {hint}")
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
