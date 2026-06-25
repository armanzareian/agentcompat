from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agentcompat.analyzer import analyze_compatibility
from agentcompat.io import (
    TRACE_FORMATS,
    InputError,
    RedactionConfig,
    iter_traces,
    load_json,
    load_tool_bundle,
)
from agentcompat.models import CompatibilityReport, TraceResult, ValidationIssue
from agentcompat.report import report_to_dict
from agentcompat.validator import UnsupportedSchemaError


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> int:
    environment = os.environ if env is None else env
    root = Path.cwd() if cwd is None else cwd
    args = _build_parser().parse_args(argv)

    try:
        config = _load_config(args.config, root)
        settings = _settings_from_sources(args, config, root)
        report = analyze_compatibility(
            load_tool_bundle(settings.baseline),
            load_tool_bundle(settings.candidate),
            iter_traces(
                settings.traces,
                max_traces=settings.max_traces,
                trace_format=settings.trace_format,
                redaction=RedactionConfig(
                    paths=tuple(settings.redact_paths),
                    key_patterns=tuple(settings.redact_key_patterns),
                ),
            ),
            sample_size=settings.sample_size,
            sample_seed=settings.sample_seed,
        )
        report_payload = report_to_dict(report)
        _write_json(settings.report_json, report_payload)
        _write_json(settings.sarif, _sarif_payload(report, settings.candidate, root))
        _append_summary(environment, _render_summary(report, settings, root))
        _append_outputs(environment, report, settings, root)
        return 0 if report.score >= settings.fail_under else 1
    except (InputError, UnsupportedSchemaError) as exc:
        print(f"agentcompat-action: {exc}", file=sys.stderr)
        _append_summary(environment, _render_error_summary(exc))
        return 2


class _ActionSettings:
    def __init__(
        self,
        *,
        baseline: Path,
        candidate: Path,
        traces: Path,
        trace_format: str,
        fail_under: float,
        max_traces: int,
        sample_size: int | None,
        sample_seed: int,
        redact_paths: tuple[str, ...],
        redact_key_patterns: tuple[str, ...],
        report_json: Path,
        sarif: Path,
    ) -> None:
        self.baseline = baseline
        self.candidate = candidate
        self.traces = traces
        self.trace_format = trace_format
        self.fail_under = fail_under
        self.max_traces = max_traces
        self.sample_size = sample_size
        self.sample_seed = sample_seed
        self.redact_paths = redact_paths
        self.redact_key_patterns = redact_key_patterns
        self.report_json = report_json
        self.sarif = sarif


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentcompat-github-action",
        description="Run AgentCompat and write GitHub Action summary, outputs, and SARIF.",
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--traces", type=Path)
    parser.add_argument("--trace-format")
    parser.add_argument("--fail-under", type=float)
    parser.add_argument("--max-traces", type=int)
    parser.add_argument("--sample-size", type=int)
    parser.add_argument("--sample-seed", type=int)
    parser.add_argument("--redact-path", action="append")
    parser.add_argument("--redact-key-pattern", action="append")
    parser.add_argument("--report-json", type=Path, default=Path("agentcompat-report.json"))
    parser.add_argument("--sarif", type=Path, default=Path("agentcompat.sarif"))
    return parser


def _load_config(path: Path | None, cwd: Path) -> dict[str, Any]:
    if path is None:
        default = cwd / ".agentcompat.json"
        if not default.exists():
            return {}
        path = default
    path = _resolve_path(cwd, path)
    if not path.exists():
        raise InputError(f"Action config file does not exist: {path}.")
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise InputError("Action config must be a JSON object.")
    return payload


def _settings_from_sources(
    args: argparse.Namespace,
    config: dict[str, Any],
    cwd: Path,
) -> _ActionSettings:
    trace_format = _string_setting(args.trace_format, config, "trace_format", "canonical")
    if trace_format not in TRACE_FORMATS:
        supported = ", ".join(sorted(TRACE_FORMATS))
        raise InputError(f"Unsupported trace format {trace_format!r}; choose one of {supported}.")

    fail_under = _float_setting(args.fail_under, config, "fail_under", 100.0)
    if not 0 <= fail_under <= 100:
        raise InputError("fail_under must be between 0 and 100.")

    max_traces = _int_setting(args.max_traces, config, "max_traces", 10_000)
    if max_traces <= 0:
        raise InputError("max_traces must be a positive integer.")

    sample_size = _optional_int_setting(args.sample_size, config, "sample_size")
    if sample_size is not None and sample_size <= 0:
        raise InputError("sample_size must be a positive integer.")

    sample_seed = _int_setting(args.sample_seed, config, "sample_seed", 0)

    baseline = _required_path(args.baseline, config, "baseline", cwd)
    candidate = _optional_path(args.candidate, config, "candidate", cwd)
    if candidate is None:
        candidate = _discover_candidate(config, cwd)
    traces = _required_path(args.traces, config, "traces", cwd)

    return _ActionSettings(
        baseline=baseline,
        candidate=candidate,
        traces=traces,
        trace_format=trace_format,
        fail_under=fail_under,
        max_traces=max_traces,
        sample_size=sample_size,
        sample_seed=sample_seed,
        redact_paths=_list_setting(args.redact_path, config, "redact_paths"),
        redact_key_patterns=_list_setting(
            args.redact_key_pattern,
            config,
            "redact_key_patterns",
        ),
        report_json=_resolve_path(cwd, args.report_json),
        sarif=_resolve_path(cwd, args.sarif),
    )


def _string_setting(
    value: str | None,
    config: dict[str, Any],
    key: str,
    default: str,
) -> str:
    if value is not None:
        return value
    configured = config.get(key, default)
    if not isinstance(configured, str):
        raise InputError(f"{key} must be a string.")
    return configured


def _float_setting(
    value: float | None,
    config: dict[str, Any],
    key: str,
    default: float,
) -> float:
    if value is not None:
        return value
    configured = config.get(key, default)
    if isinstance(configured, bool) or not isinstance(configured, (int, float)):
        raise InputError(f"{key} must be a number.")
    return float(configured)


def _int_setting(
    value: int | None,
    config: dict[str, Any],
    key: str,
    default: int,
) -> int:
    if value is not None:
        return value
    configured = config.get(key, default)
    if isinstance(configured, bool) or not isinstance(configured, int):
        raise InputError(f"{key} must be an integer.")
    return configured


def _optional_int_setting(
    value: int | None,
    config: dict[str, Any],
    key: str,
) -> int | None:
    if value is not None:
        return value
    configured = config.get(key)
    if configured is None:
        return None
    if isinstance(configured, bool) or not isinstance(configured, int):
        raise InputError(f"{key} must be an integer.")
    return configured


def _list_setting(
    values: list[str] | None,
    config: dict[str, Any],
    key: str,
) -> tuple[str, ...]:
    if values is not None:
        return tuple(value for value in values if value)
    configured = config.get(key, [])
    if not isinstance(configured, list) or not all(isinstance(value, str) for value in configured):
        raise InputError(f"{key} must be a list of strings.")
    return tuple(configured)


def _required_path(
    value: Path | None,
    config: dict[str, Any],
    key: str,
    cwd: Path,
) -> Path:
    path = _optional_path(value, config, key, cwd)
    if path is None:
        raise InputError(f"Missing required action input: {key}.")
    return path


def _optional_path(
    value: Path | None,
    config: dict[str, Any],
    key: str,
    cwd: Path,
) -> Path | None:
    if value is not None:
        return _resolve_path(cwd, value)
    configured = config.get(key)
    if configured is None:
        return None
    if not isinstance(configured, str):
        raise InputError(f"{key} must be a path string.")
    return _resolve_path(cwd, Path(configured))


def _discover_candidate(config: dict[str, Any], cwd: Path) -> Path:
    discovery = config.get("changed_schema_discovery")
    if not isinstance(discovery, dict) or not discovery.get("enabled", False):
        raise InputError("Missing required action input: candidate.")

    globs = discovery.get("globs")
    if not isinstance(globs, list) or not all(isinstance(pattern, str) for pattern in globs):
        raise InputError("changed_schema_discovery.globs must be a list of strings.")

    matches: set[Path] = set()
    for pattern in globs:
        matches.update(path for path in cwd.glob(pattern) if path.is_file())
    if not matches:
        raise InputError("changed_schema_discovery matched no candidate schema files.")
    if len(matches) > 1:
        rendered = ", ".join(_display_path(path, cwd) for path in sorted(matches))
        raise InputError(
            f"changed_schema_discovery matched multiple candidate schemas: {rendered}."
        )
    return next(iter(matches)).resolve()


def _resolve_path(cwd: Path, path: Path) -> Path:
    return path if path.is_absolute() else (cwd / path).resolve()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_summary(env: Mapping[str, str], markdown: str) -> None:
    path_value = env.get("GITHUB_STEP_SUMMARY")
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(markdown)
        if not markdown.endswith("\n"):
            handle.write("\n")


def _append_outputs(
    env: Mapping[str, str],
    report: CompatibilityReport,
    settings: _ActionSettings,
    cwd: Path,
) -> None:
    path_value = env.get("GITHUB_OUTPUT")
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    outputs = {
        "score": f"{report.score:.2f}",
        "passed": str(report.passed),
        "broken": str(report.broken),
        "excluded": str(report.excluded),
        "report-json": _display_path(settings.report_json, cwd),
        "sarif": _display_path(settings.sarif, cwd),
    }
    with path.open("a", encoding="utf-8") as handle:
        for name, value in outputs.items():
            handle.write(f"{name}={value}\n")


def _render_summary(report: CompatibilityReport, settings: _ActionSettings, cwd: Path) -> str:
    passed_policy = report.score >= settings.fail_under
    broken_results = [result for result in report.results if result.status == "broken"]
    lines = [
        "# AgentCompat compatibility",
        "",
        f"Score: {report.score:.2f}/100",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Policy threshold | {settings.fail_under:.2f} |",
        f"| Policy result | {'pass' if passed_policy else 'fail'} |",
        f"| Passed calls | {report.passed} |",
        f"| Broken calls | {report.broken} |",
        f"| Excluded calls | {report.excluded} |",
        f"| Compatible weight | {report.passing_weight:g}/{report.eligible_weight:g} |",
    ]
    if report.sampling is not None:
        lines.extend(
            [
                (
                    "| Sampling | "
                    f"{report.sampling.sampled}/{report.sampling.population} calls, "
                    f"seed {report.sampling.seed} |"
                ),
                (
                    "| Sampled weight | "
                    f"{report.sampling.sampled_weight:g}/"
                    f"{report.sampling.population_weight:g} |"
                ),
                "",
                (
                    "Sampling: "
                    f"{report.sampling.sampled}/{report.sampling.population} calls selected "
                    f"with seed {report.sampling.seed}"
                ),
                "",
            ]
        )
    else:
        lines.append("")

    if broken_results:
        lines.extend(["## Broken traces", ""])
        for result in broken_results:
            lines.append(
                "- "
                f"[{result.trace.trace_id}](#trace-{_slug(result.trace.trace_id)}) "
                f"`{result.trace.tool}` "
                f"({len(result.issues)} issue(s), weight {result.trace.weight:g})"
            )
        lines.append("")
        for result in broken_results[:10]:
            lines.extend(_trace_detail_lines(result))
    else:
        lines.extend(["No broken eligible traces.", ""])

    if report.migration_plan:
        lines.extend(["## Migration highlights", ""])
        for item in report.migration_plan[:5]:
            lines.append(
                f"- `{item.change_id}` affects {len(item.trace_ids)} trace(s) "
                f"and weight {item.affected_weight:g}: {item.guidance}"
            )
        lines.append("")

    lines.extend(
        [
            f"JSON report: `{_display_path(settings.report_json, cwd)}`",
            f"SARIF report: `{_display_path(settings.sarif, cwd)}`",
            "",
        ]
    )
    return "\n".join(lines)


def _trace_detail_lines(result: TraceResult) -> list[str]:
    lines = [
        f'<a id="trace-{_slug(result.trace.trace_id)}"></a>',
        f"### Trace `{result.trace.trace_id}`",
        "",
        f"- Tool: `{result.trace.tool}`",
    ]
    for issue in result.issues:
        lines.append(f"- `{issue.code}` at `{issue.path}`: {issue.message}")
        if issue.change_ids:
            lines.append(f"- Change IDs: {', '.join(f'`{value}`' for value in issue.change_ids)}")
    lines.append("")
    return lines


def _render_error_summary(exc: Exception) -> str:
    return "\n".join(
        [
            "# AgentCompat compatibility",
            "",
            "Input error",
            "",
            str(exc),
            "",
        ]
    )


def _sarif_payload(report: CompatibilityReport, candidate: Path, cwd: Path) -> dict[str, Any]:
    rules = {
        issue.code: {
            "id": issue.code,
            "shortDescription": {"text": issue.code.replace("_", " ")},
        }
        for issue in _iter_broken_issues(report)
    }
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "AgentCompat",
                        "informationUri": "https://github.com/armanzareian/agentcompat",
                        "rules": [rules[key] for key in sorted(rules)],
                    }
                },
                "results": [
                    _sarif_result(result, issue, candidate, cwd)
                    for result in report.results
                    if result.status == "broken"
                    for issue in result.issues
                ],
            }
        ],
    }


def _iter_broken_issues(report: CompatibilityReport) -> list[ValidationIssue]:
    return [
        issue for result in report.results if result.status == "broken" for issue in result.issues
    ]


def _sarif_result(
    result: TraceResult,
    issue: ValidationIssue,
    candidate: Path,
    cwd: Path,
) -> dict[str, Any]:
    fingerprint = hashlib.sha256(
        f"{result.trace.trace_id}:{result.trace.tool}:{issue.code}:{issue.path}".encode()
    ).hexdigest()
    return {
        "ruleId": issue.code,
        "level": "error",
        "message": {
            "text": (
                f"{result.trace.trace_id} calling {result.trace.tool}: "
                f"{issue.message} at {issue.path}."
            )
        },
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": _display_path(candidate, cwd)},
                    "region": {"startLine": 1},
                }
            }
        ],
        "partialFingerprints": {"agentcompat": fingerprint},
        "properties": {
            "trace_id": result.trace.trace_id,
            "tool": result.trace.tool,
            "path": issue.path,
            "change_ids": list(issue.change_ids),
        },
    }


def _display_path(path: Path, cwd: Path) -> str:
    try:
        return path.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "trace"


if __name__ == "__main__":
    raise SystemExit(main())
