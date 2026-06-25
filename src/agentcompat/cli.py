from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from agentcompat.analyzer import analyze_compatibility
from agentcompat.evaluation import evaluate_report
from agentcompat.io import (
    TRACE_FORMATS,
    InputError,
    RedactionConfig,
    iter_traces,
    load_json,
    load_tool_bundle,
)
from agentcompat.models import EvaluationMetrics
from agentcompat.report import (
    evaluation_to_dict,
    render_text,
    report_to_dict,
)
from agentcompat.validator import (
    UnsupportedSchemaError,
    audit_tool_bundle,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check":
            return _run_check(args)
        if args.command == "audit":
            return _run_audit(args)
        if args.command == "eval":
            return _run_evaluation(args)
    except (InputError, UnsupportedSchemaError) as exc:
        print(f"agentcompat: {exc}", file=sys.stderr)
        return 2
    parser.error("a command is required")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentcompat",
        description="Replay observed LLM tool calls against evolving JSON schemas.",
    )
    parser.add_argument("--version", action="version", version="agentcompat 0.1.0")
    subparsers = parser.add_subparsers(dest="command")

    check = subparsers.add_parser("check", help="score candidate schema compatibility")
    check.add_argument("--baseline", type=Path, required=True)
    check.add_argument("--candidate", type=Path, required=True)
    check.add_argument("--traces", type=Path, required=True)
    check.add_argument("--trace-format", choices=sorted(TRACE_FORMATS), default="canonical")
    check.add_argument("--redact-path", action="append", default=[])
    check.add_argument("--redact-key-pattern", action="append", default=[])
    check.add_argument("--format", choices=("text", "json"), default="text")
    check.add_argument("--fail-under", type=_score, default=100.0)
    check.add_argument("--max-traces", type=int, default=10_000)
    check.add_argument("--sample-size", type=_positive_int)
    check.add_argument("--sample-seed", type=int, default=0)

    audit = subparsers.add_parser(
        "audit",
        help="inventory unsupported JSON Schema semantics",
    )
    audit.add_argument("--schema", type=Path, required=True)
    audit.add_argument("--format", choices=("text", "json"), default="text")

    evaluate = subparsers.add_parser("eval", help="run a labeled evaluation suite")
    evaluate.add_argument("--suite", type=Path, required=True)
    evaluate.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _run_check(args: argparse.Namespace) -> int:
    report = analyze_compatibility(
        load_tool_bundle(args.baseline),
        load_tool_bundle(args.candidate),
        iter_traces(
            args.traces,
            max_traces=args.max_traces,
            trace_format=args.trace_format,
            redaction=RedactionConfig(
                paths=tuple(args.redact_path),
                key_patterns=tuple(args.redact_key_pattern),
            ),
        ),
        sample_size=args.sample_size,
        sample_seed=args.sample_seed,
    )
    if args.format == "json":
        print(json.dumps(report_to_dict(report), indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0 if report.score >= args.fail_under else 1


def _run_audit(args: argparse.Namespace) -> int:
    issues = audit_tool_bundle(load_tool_bundle(args.schema))
    if args.format == "json":
        print(
            json.dumps(
                {
                    "supported": not issues,
                    "unsupported": [
                        {
                            "keyword": issue.keyword,
                            "schema_path": issue.schema_path,
                        }
                        for issue in issues
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        if not issues:
            print("All encountered JSON Schema keywords are supported.")
        else:
            print("Unsupported JSON Schema semantics:")
            for issue in issues:
                print(f"- {issue.keyword} at {issue.schema_path}")
    return 0 if not issues else 1


def _run_evaluation(args: argparse.Namespace) -> int:
    payload = load_json(args.suite)
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise InputError("Evaluation suite must contain a 'cases' list.")

    case_results: list[dict[str, object]] = []
    totals = {"tp": 0, "fp": 0, "fn": 0, "expected": 0, "causes": 0.0}
    for raw_case in payload["cases"]:
        if not isinstance(raw_case, dict):
            raise InputError("Every evaluation case must be an object.")
        metrics = _evaluate_case(args.suite.parent, raw_case)
        name = raw_case.get("name")
        case_results.append(
            {
                "name": name if isinstance(name, str) else "unnamed",
                "metrics": evaluation_to_dict(metrics),
            }
        )
        expected = raw_case.get("expected_breakages")
        expected_count = len(expected) if isinstance(expected, dict) else 0
        totals["tp"] += metrics.true_positives
        totals["fp"] += metrics.false_positives
        totals["fn"] += metrics.false_negatives
        totals["expected"] += expected_count
        totals["causes"] += metrics.root_cause_accuracy * expected_count

    aggregate = _aggregate_metrics(totals)
    result = {
        "aggregate": evaluation_to_dict(aggregate),
        "cases": case_results,
    }
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("AgentCompat evaluation")
        print(f"Cases: {len(case_results)}")
        print(f"Precision: {aggregate.precision:.4f}")
        print(f"Recall: {aggregate.recall:.4f}")
        print(f"F1: {aggregate.f1:.4f}")
        print(f"Root-cause accuracy: {aggregate.root_cause_accuracy:.4f}")
    return 0


def _evaluate_case(root: Path, raw_case: dict[str, object]) -> EvaluationMetrics:
    expected = raw_case.get("expected_breakages")
    if not isinstance(expected, dict) or not all(
        isinstance(key, str)
        and isinstance(value, list)
        and all(isinstance(code, str) for code in value)
        for key, value in expected.items()
    ):
        raise InputError("Evaluation case expected_breakages must map trace IDs to codes.")

    report = analyze_compatibility(
        load_tool_bundle(_case_path(root, raw_case, "baseline")),
        load_tool_bundle(_case_path(root, raw_case, "candidate")),
        iter_traces(_case_path(root, raw_case, "traces")),
    )
    return evaluate_report(report, expected)


def _case_path(root: Path, case: dict[str, object], key: str) -> Path:
    value = case.get(key)
    if not isinstance(value, str):
        raise InputError(f"Evaluation case is missing a {key!r} path.")
    return root / value


def _aggregate_metrics(totals: dict[str, int | float]) -> EvaluationMetrics:
    tp = int(totals["tp"])
    fp = int(totals["fp"])
    fn = int(totals["fn"])
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    expected = int(totals["expected"])
    root_cause = float(totals["causes"]) / expected if expected else 0.0
    return EvaluationMetrics(
        round(precision, 4),
        round(recall, 4),
        round(f1, 4),
        round(root_cause, 4),
        tp,
        fp,
        fn,
    )


def _score(value: str) -> float:
    score = float(value)
    if not 0 <= score <= 100:
        raise argparse.ArgumentTypeError("score must be between 0 and 100")
    return score


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed
