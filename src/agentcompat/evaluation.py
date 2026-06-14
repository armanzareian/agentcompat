from __future__ import annotations

from agentcompat.models import CompatibilityReport, EvaluationMetrics


def evaluate_report(
    report: CompatibilityReport,
    expected_breakages: dict[str, list[str]],
) -> EvaluationMetrics:
    expected_ids = set(expected_breakages)
    broken_results = {
        result.trace.trace_id: result for result in report.results if result.status == "broken"
    }
    predicted_ids = set(broken_results)

    true_positives = len(expected_ids & predicted_ids)
    false_positives = len(predicted_ids - expected_ids)
    false_negatives = len(expected_ids - predicted_ids)
    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)
    f1 = _ratio(2 * precision * recall, precision + recall)

    correct_causes = 0
    for trace_id, expected_codes in expected_breakages.items():
        result = broken_results.get(trace_id)
        if result is None:
            continue
        actual_codes = {issue.code for issue in result.issues}
        if set(expected_codes).issubset(actual_codes):
            correct_causes += 1

    root_cause_accuracy = _ratio(correct_causes, len(expected_breakages))
    return EvaluationMetrics(
        round(precision, 4),
        round(recall, 4),
        round(f1, 4),
        round(root_cause_accuracy, 4),
        true_positives,
        false_positives,
        false_negatives,
    )


def _ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator
