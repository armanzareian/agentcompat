from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from typing import Any, TypeGuard

from agentcompat.models import (
    MigrationPlanItem,
    SchemaChange,
    TraceResult,
    ValidationIssue,
)

Schema = dict[str, Any] | bool

_MINIMUM_CONSTRAINTS = ("minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties")
_MAXIMUM_CONSTRAINTS = ("maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties")
_ISSUE_KEYWORDS = {
    "minimum": "minimum",
    "maximum": "maximum",
    "exclusive_minimum": "exclusiveMinimum",
    "exclusive_maximum": "exclusiveMaximum",
    "min_length": "minLength",
    "max_length": "maxLength",
    "min_items": "minItems",
    "max_items": "maxItems",
    "min_properties": "minProperties",
    "max_properties": "maxProperties",
    "multiple_of": "multipleOf",
    "pattern_mismatch": "pattern",
    "format_mismatch": "format",
    "const_mismatch": "const",
    "unique_items": "uniqueItems",
    "false_schema": "schema",
}
_ISSUE_KINDS = {
    "tool_removed": "tool_removed",
    "missing_required": "required_added",
    "unexpected_property": "property_removed",
    "type_mismatch": "type_changed",
    "enum_mismatch": "enum_narrowed",
}


def compare_tool_bundles(
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
) -> tuple[SchemaChange, ...]:
    changes: list[SchemaChange] = []
    for tool in sorted(baseline):
        if tool not in candidate:
            changes.append(
                _make_change(
                    "tool_removed",
                    tool,
                    "$",
                    "tool",
                    baseline[tool],
                    None,
                )
            )
            continue
        _compare_schema(tool, "$", baseline[tool], candidate[tool], changes)
    unique_changes = {change.change_id: change for change in changes}
    return tuple(
        sorted(
            unique_changes.values(),
            key=lambda change: (
                change.tool,
                change.path,
                change.kind,
                change.keyword,
                change.change_id,
            ),
        )
    )


def attribute_issues(
    tool: str,
    issues: tuple[ValidationIssue, ...],
    changes: tuple[SchemaChange, ...],
) -> tuple[ValidationIssue, ...]:
    relevant = tuple(change for change in changes if change.tool == tool)
    attributed: list[ValidationIssue] = []
    for issue in issues:
        path = _normalize_instance_path(issue.path)
        kind = _ISSUE_KINDS.get(issue.code, "constraint_tightened")
        keyword = _ISSUE_KEYWORDS.get(issue.code)
        matches = [
            change
            for change in relevant
            if change.kind == kind
            and change.path == path
            and (keyword is None or change.keyword == keyword)
        ]
        if issue.code == "unexpected_property" and not matches:
            parent = _parent_path(path)
            matches = [
                change
                for change in relevant
                if change.kind == "constraint_tightened"
                and change.path == parent
                and change.keyword == "additionalProperties"
            ]
        attributed.append(
            replace(
                issue,
                change_ids=tuple(sorted(change.change_id for change in matches)),
            )
        )
    return tuple(attributed)


def build_migration_plan(
    changes: tuple[SchemaChange, ...],
    results: tuple[TraceResult, ...],
) -> tuple[MigrationPlanItem, ...]:
    by_id = {change.change_id: change for change in changes}
    trace_ids: dict[str, set[str]] = {}
    affected_weight: dict[str, float] = {}
    issue_count: dict[str, int] = {}

    for result in results:
        seen_for_trace: set[str] = set()
        for issue in result.issues:
            for change_id in issue.change_ids:
                issue_count[change_id] = issue_count.get(change_id, 0) + 1
                if change_id in seen_for_trace:
                    continue
                seen_for_trace.add(change_id)
                trace_ids.setdefault(change_id, set()).add(result.trace.trace_id)
                affected_weight[change_id] = (
                    affected_weight.get(change_id, 0.0) + result.trace.weight
                )

    plan = [
        MigrationPlanItem(
            change_id=change_id,
            kind=change.kind,
            tool=change.tool,
            path=change.path,
            keyword=change.keyword,
            affected_weight=affected_weight[change_id],
            trace_ids=tuple(sorted(trace_ids[change_id])),
            issue_count=issue_count[change_id],
            guidance=_migration_guidance(change),
        )
        for change_id, change in by_id.items()
        if change_id in trace_ids
    ]
    return tuple(
        sorted(
            plan,
            key=lambda item: (
                -item.affected_weight,
                -len(item.trace_ids),
                item.tool,
                item.path,
                item.kind,
                item.change_id,
            ),
        )
    )


def _compare_schema(
    tool: str,
    path: str,
    baseline: Schema,
    candidate: Schema,
    changes: list[SchemaChange],
) -> None:
    if candidate is False and baseline is not False:
        changes.append(
            _make_change(
                "constraint_tightened",
                tool,
                path,
                "schema",
                baseline,
                candidate,
            )
        )
        return
    if isinstance(baseline, bool) or isinstance(candidate, bool):
        return

    _compare_types(tool, path, baseline, candidate, changes)
    _compare_enum(tool, path, baseline, candidate, changes)
    _compare_constraints(tool, path, baseline, candidate, changes)

    baseline_required = _string_set(baseline.get("required"))
    candidate_required = _string_set(candidate.get("required"))
    for property_name in sorted(candidate_required - baseline_required):
        changes.append(
            _make_change(
                "required_added",
                tool,
                _child_path(path, property_name),
                "required",
                False,
                True,
            )
        )

    baseline_properties = _schema_map(baseline.get("properties"))
    candidate_properties = _schema_map(candidate.get("properties"))
    for property_name in sorted(baseline_properties.keys() - candidate_properties.keys()):
        changes.append(
            _make_change(
                "property_removed",
                tool,
                _child_path(path, property_name),
                "properties",
                baseline_properties[property_name],
                None,
            )
        )
    for property_name in sorted(baseline_properties.keys() & candidate_properties.keys()):
        _compare_schema(
            tool,
            _child_path(path, property_name),
            baseline_properties[property_name],
            candidate_properties[property_name],
            changes,
        )

    _compare_schema_keyword(tool, path, "items", baseline, candidate, changes)
    for keyword in ("allOf", "anyOf", "oneOf", "prefixItems"):
        baseline_branches = _schema_list(baseline.get(keyword))
        candidate_branches = _schema_list(candidate.get(keyword))
        for index, (baseline_branch, candidate_branch) in enumerate(
            zip(baseline_branches, candidate_branches, strict=False)
        ):
            branch_path = path if keyword != "prefixItems" else f"{path}[{index}]"
            _compare_schema(tool, branch_path, baseline_branch, candidate_branch, changes)


def _compare_types(
    tool: str,
    path: str,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    changes: list[SchemaChange],
) -> None:
    if "type" not in candidate:
        return
    baseline_types = _types(baseline.get("type"))
    candidate_types = _types(candidate.get("type"))
    if baseline_types and all(
        any(_type_covers(candidate_type, baseline_type) for candidate_type in candidate_types)
        for baseline_type in baseline_types
    ):
        return
    if baseline.get("type") != candidate.get("type"):
        changes.append(
            _make_change(
                "type_changed",
                tool,
                path,
                "type",
                baseline.get("type"),
                candidate.get("type"),
            )
        )


def _compare_enum(
    tool: str,
    path: str,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    changes: list[SchemaChange],
) -> None:
    baseline_enum = baseline.get("enum")
    candidate_enum = candidate.get("enum")
    if not isinstance(baseline_enum, list) or not isinstance(candidate_enum, list):
        return
    candidate_values = {_canonical(value) for value in candidate_enum}
    if any(_canonical(value) not in candidate_values for value in baseline_enum):
        changes.append(
            _make_change(
                "enum_narrowed",
                tool,
                path,
                "enum",
                baseline_enum,
                candidate_enum,
            )
        )


def _compare_constraints(
    tool: str,
    path: str,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    changes: list[SchemaChange],
) -> None:
    for keyword in _MINIMUM_CONSTRAINTS:
        before = baseline.get(keyword)
        after = candidate.get(keyword)
        if not _number(after):
            continue
        if not _number(before) or after > before:
            changes.append(_make_change("constraint_tightened", tool, path, keyword, before, after))
    for keyword in _MAXIMUM_CONSTRAINTS:
        before = baseline.get(keyword)
        after = candidate.get(keyword)
        if not _number(after):
            continue
        if not _number(before) or after < before:
            changes.append(_make_change("constraint_tightened", tool, path, keyword, before, after))

    before_multiple = baseline.get("multipleOf")
    after_multiple = candidate.get("multipleOf")
    if _multiple_tightened(before_multiple, after_multiple):
        changes.append(
            _make_change(
                "constraint_tightened",
                tool,
                path,
                "multipleOf",
                before_multiple,
                after_multiple,
            )
        )

    for keyword in ("const", "pattern", "format"):
        if keyword in candidate and candidate.get(keyword) != baseline.get(keyword):
            changes.append(
                _make_change(
                    "constraint_tightened",
                    tool,
                    path,
                    keyword,
                    baseline.get(keyword),
                    candidate.get(keyword),
                )
            )

    if candidate.get("uniqueItems") is True and baseline.get("uniqueItems") is not True:
        changes.append(
            _make_change(
                "constraint_tightened",
                tool,
                path,
                "uniqueItems",
                baseline.get("uniqueItems"),
                True,
            )
        )
    if (
        candidate.get("additionalProperties") is False
        and baseline.get("additionalProperties", True) is not False
    ):
        changes.append(
            _make_change(
                "constraint_tightened",
                tool,
                path,
                "additionalProperties",
                baseline.get("additionalProperties", True),
                False,
            )
        )


def _compare_schema_keyword(
    tool: str,
    path: str,
    keyword: str,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    changes: list[SchemaChange],
) -> None:
    baseline_schema = baseline.get(keyword)
    candidate_schema = candidate.get(keyword)
    if isinstance(baseline_schema, (dict, bool)) and isinstance(candidate_schema, (dict, bool)):
        _compare_schema(tool, f"{path}[*]", baseline_schema, candidate_schema, changes)


def _make_change(
    kind: str,
    tool: str,
    path: str,
    keyword: str,
    before: Any,
    after: Any,
) -> SchemaChange:
    identity = {
        "kind": kind,
        "tool": tool,
        "path": path,
        "keyword": keyword,
        "before": before,
        "after": after,
    }
    digest = hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()[:16]
    return SchemaChange(
        change_id=f"chg_{digest}",
        kind=kind,
        tool=tool,
        path=path,
        keyword=keyword,
        before=before,
        after=after,
        description=_change_description(kind, tool, path, keyword),
    )


def _change_description(kind: str, tool: str, path: str, keyword: str) -> str:
    descriptions = {
        "tool_removed": f"Tool {tool!r} was removed.",
        "required_added": f"{path} became required for tool {tool!r}.",
        "property_removed": f"{path} was removed from tool {tool!r}.",
        "type_changed": f"The accepted type at {path} changed for tool {tool!r}.",
        "enum_narrowed": f"The accepted enum at {path} narrowed for tool {tool!r}.",
    }
    return descriptions.get(
        kind,
        f"The {keyword} constraint at {path} tightened for tool {tool!r}.",
    )


def _migration_guidance(change: SchemaChange) -> str:
    guidance = {
        "tool_removed": (
            f"Migrate calls from {change.tool!r} to a replacement or retain a compatibility alias."
        ),
        "required_added": (
            f"Populate {change.path} for {change.tool!r} calls or keep the field optional "
            "during migration."
        ),
        "property_removed": (
            f"Remove or map {change.path} before invoking {change.tool!r}, or retain compatibility "
            "support."
        ),
        "type_changed": (
            f"Convert values at {change.path} to the candidate type before invoking "
            f"{change.tool!r}."
        ),
        "enum_narrowed": (
            f"Map retired values at {change.path} to accepted enum members for {change.tool!r}."
        ),
    }
    return guidance.get(
        change.kind,
        f"Adjust {change.path} to satisfy {change.keyword}={change.after!r}, or relax the "
        "candidate constraint during migration.",
    )


def _types(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _type_covers(candidate_type: str, baseline_type: str) -> bool:
    return candidate_type == baseline_type or (
        candidate_type == "number" and baseline_type == "integer"
    )


def _schema_map(value: Any) -> dict[str, Schema]:
    if not isinstance(value, dict):
        return {}
    return {
        key: schema
        for key, schema in value.items()
        if isinstance(key, str) and isinstance(schema, (dict, bool))
    }


def _schema_list(value: Any) -> list[Schema]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, (dict, bool))]


def _string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str)}


def _multiple_tightened(before: Any, after: Any) -> bool:
    if not _number(after) or after <= 0:
        return False
    if not _number(before) or before <= 0:
        return True
    ratio = after / before
    return ratio >= 1 and float(ratio).is_integer() and after != before


def _number(value: Any) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _normalize_instance_path(path: str) -> str:
    return re.sub(r"\[\d+\]", "[*]", path)


def _parent_path(path: str) -> str:
    if path.endswith("]"):
        bracket = path.rfind("[")
        if bracket > 0:
            return path[:bracket]
    dot = path.rfind(".")
    return path[:dot] if dot > 0 else "$"


def _child_path(parent: str, key: str) -> str:
    if key.isidentifier():
        return f"{parent}.{key}"
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'{parent}["{escaped}"]'
