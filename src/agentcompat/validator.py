from __future__ import annotations

from typing import Any

from agentcompat.models import ValidationIssue


def validate_instance(instance: Any, schema: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    _validate(instance, schema, "$", issues)
    return issues


def _validate(
    instance: Any,
    schema: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    if "allOf" in schema:
        for branch in _schema_list(schema["allOf"]):
            _validate(instance, branch, path, issues)

    if "anyOf" in schema:
        branches = _schema_list(schema["anyOf"])
        if branches and not any(not validate_instance(instance, branch) for branch in branches):
            issues.append(
                ValidationIssue(
                    code="any_of_mismatch",
                    path=path,
                    message="Value does not match any allowed schema.",
                    expected=len(branches),
                    actual=instance,
                )
            )
            return

    if "oneOf" in schema:
        branches = _schema_list(schema["oneOf"])
        matches = sum(not validate_instance(instance, branch) for branch in branches)
        if matches != 1:
            issues.append(
                ValidationIssue(
                    code="one_of_mismatch",
                    path=path,
                    message="Value must match exactly one allowed schema.",
                    expected=1,
                    actual=matches,
                )
            )
            return

    if "const" in schema and instance != schema["const"]:
        issues.append(
            ValidationIssue(
                code="const_mismatch",
                path=path,
                message="Value does not match the required constant.",
                expected=schema["const"],
                actual=instance,
            )
        )

    enum = schema.get("enum")
    if isinstance(enum, list) and instance not in enum:
        issues.append(
            ValidationIssue(
                code="enum_mismatch",
                path=path,
                message="Value is not one of the allowed options.",
                expected=enum,
                actual=instance,
            )
        )

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(instance, expected_type):
        issues.append(
            ValidationIssue(
                code="type_mismatch",
                path=path,
                message="Value has an incompatible JSON type.",
                expected=expected_type,
                actual=_json_type(instance),
            )
        )
        return

    if isinstance(instance, dict):
        _validate_object(instance, schema, path, issues)
    elif isinstance(instance, list):
        _validate_array(instance, schema, path, issues)
    elif isinstance(instance, str):
        _validate_string(instance, schema, path, issues)
    elif _is_number(instance):
        _validate_number(instance, schema, path, issues)


def _validate_object(
    instance: dict[str, Any],
    schema: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    properties = schema.get("properties")
    defined = properties if isinstance(properties, dict) else {}

    required = schema.get("required")
    if isinstance(required, list):
        for key in required:
            if isinstance(key, str) and key not in instance:
                issues.append(
                    ValidationIssue(
                        code="missing_required",
                        path=_child_path(path, key),
                        message=f"Required property {key!r} is missing.",
                        expected="present",
                        actual="missing",
                    )
                )

    for key, value in instance.items():
        property_schema = defined.get(key)
        if isinstance(property_schema, dict):
            _validate(value, property_schema, _child_path(path, key), issues)

    additional = schema.get("additionalProperties", True)
    for key, value in instance.items():
        if key in defined:
            continue
        child_path = _child_path(path, key)
        if additional is False:
            issues.append(
                ValidationIssue(
                    code="unexpected_property",
                    path=child_path,
                    message=f"Property {key!r} is not accepted by this schema.",
                    expected=sorted(defined),
                    actual=key,
                )
            )
        elif isinstance(additional, dict):
            _validate(value, additional, child_path, issues)

    _append_length_issue(
        len(instance),
        schema,
        path,
        issues,
        minimum_key="minProperties",
        maximum_key="maxProperties",
        minimum_code="min_properties",
        maximum_code="max_properties",
        noun="properties",
    )


def _validate_array(
    instance: list[Any],
    schema: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    _append_length_issue(
        len(instance),
        schema,
        path,
        issues,
        minimum_key="minItems",
        maximum_key="maxItems",
        minimum_code="min_items",
        maximum_code="max_items",
        noun="items",
    )

    item_schema = schema.get("items")
    if isinstance(item_schema, dict):
        for index, item in enumerate(instance):
            _validate(item, item_schema, f"{path}[{index}]", issues)


def _validate_string(
    instance: str,
    schema: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    _append_length_issue(
        len(instance),
        schema,
        path,
        issues,
        minimum_key="minLength",
        maximum_key="maxLength",
        minimum_code="min_length",
        maximum_code="max_length",
        noun="characters",
    )


def _validate_number(
    instance: int | float,
    schema: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    bounds = (
        ("minimum", "minimum", lambda bound: instance < bound),
        ("maximum", "maximum", lambda bound: instance > bound),
        ("exclusiveMinimum", "exclusive_minimum", lambda bound: instance <= bound),
        ("exclusiveMaximum", "exclusive_maximum", lambda bound: instance >= bound),
    )
    for keyword, code, comparison in bounds:
        bound = schema.get(keyword)
        if _is_number(bound) and comparison(bound):
            issues.append(
                ValidationIssue(
                    code=code,
                    path=path,
                    message=f"Numeric value violates {keyword}.",
                    expected=bound,
                    actual=instance,
                )
            )


def _append_length_issue(
    actual: int,
    schema: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
    *,
    minimum_key: str,
    maximum_key: str,
    minimum_code: str,
    maximum_code: str,
    noun: str,
) -> None:
    minimum = schema.get(minimum_key)
    if isinstance(minimum, int) and actual < minimum:
        issues.append(
            ValidationIssue(
                code=minimum_code,
                path=path,
                message=f"Value must contain at least {minimum} {noun}.",
                expected=minimum,
                actual=actual,
            )
        )

    maximum = schema.get(maximum_key)
    if isinstance(maximum, int) and actual > maximum:
        issues.append(
            ValidationIssue(
                code=maximum_code,
                path=path,
                message=f"Value must contain at most {maximum} {noun}.",
                expected=maximum,
                actual=actual,
            )
        )


def _matches_type(instance: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(instance, candidate) for candidate in expected)
    if not isinstance(expected, str):
        return True

    checks = {
        "null": instance is None,
        "boolean": isinstance(instance, bool),
        "object": isinstance(instance, dict),
        "array": isinstance(instance, list),
        "string": isinstance(instance, str),
        "integer": isinstance(instance, int) and not isinstance(instance, bool),
        "number": _is_number(instance),
    }
    return checks.get(expected, True)


def _json_type(instance: Any) -> str:
    if instance is None:
        return "null"
    if isinstance(instance, bool):
        return "boolean"
    if isinstance(instance, dict):
        return "object"
    if isinstance(instance, list):
        return "array"
    if isinstance(instance, str):
        return "string"
    if isinstance(instance, int):
        return "integer"
    if isinstance(instance, float):
        return "number"
    return type(instance).__name__


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _schema_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _child_path(parent: str, key: str) -> str:
    if key.isidentifier():
        return f"{parent}.{key}"
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'{parent}["{escaped}"]'
