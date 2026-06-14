from __future__ import annotations

import ipaddress
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from agentcompat.models import ValidationIssue

Schema = dict[str, Any] | bool

SUPPORTED_KEYWORDS = frozenset(
    {
        "$defs",
        "$ref",
        "additionalItems",
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "definitions",
        "else",
        "enum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "if",
        "items",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "oneOf",
        "pattern",
        "prefixItems",
        "properties",
        "required",
        "then",
        "type",
        "uniqueItems",
    }
)
ANNOTATION_KEYWORDS = frozenset(
    {
        "$comment",
        "$schema",
        "contentEncoding",
        "contentMediaType",
        "default",
        "deprecated",
        "description",
        "examples",
        "readOnly",
        "title",
        "writeOnly",
    }
)
SUPPORTED_TYPES = frozenset({"array", "boolean", "integer", "null", "number", "object", "string"})
SUPPORTED_FORMATS = frozenset(
    {
        "date",
        "date-time",
        "email",
        "hostname",
        "ipv4",
        "ipv6",
        "time",
        "uri",
        "uuid",
    }
)
_SCHEMA_MAP_KEYWORDS = frozenset(
    {
        "$defs",
        "definitions",
        "dependentSchemas",
        "patternProperties",
        "properties",
    }
)
_SCHEMA_ARRAY_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_VALUE_KEYWORDS = frozenset(
    {
        "additionalItems",
        "additionalProperties",
        "contains",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)


@dataclass(frozen=True, slots=True)
class KeywordSupportIssue:
    keyword: str
    schema_path: str


class UnsupportedSchemaError(ValueError):
    def __init__(self, issues: list[KeywordSupportIssue]) -> None:
        self.issues = tuple(issues)
        details = ", ".join(f"{issue.keyword} at {issue.schema_path}" for issue in self.issues)
        super().__init__(f"Unsupported JSON Schema semantics: {details}.")


def audit_schema(
    schema: Schema,
    *,
    schema_path: str = "$",
) -> list[KeywordSupportIssue]:
    issues: list[KeywordSupportIssue] = []
    _audit_schema(schema, schema_path, issues)
    return issues


def audit_tool_bundle(
    tools: dict[str, dict[str, Any]],
) -> list[KeywordSupportIssue]:
    issues: list[KeywordSupportIssue] = []
    for name, schema in tools.items():
        _audit_schema(schema, _child_path("$", name), issues)
    return issues


def validate_instance(instance: Any, schema: Schema) -> list[ValidationIssue]:
    unsupported = audit_schema(schema)
    if unsupported:
        raise UnsupportedSchemaError(unsupported)
    issues: list[ValidationIssue] = []
    _validate(instance, schema, "$", issues)
    return issues


def _audit_schema(
    schema: Schema,
    schema_path: str,
    issues: list[KeywordSupportIssue],
) -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        return

    for keyword, value in schema.items():
        keyword_path = _child_path(schema_path, keyword)
        if (
            keyword not in SUPPORTED_KEYWORDS
            and keyword not in ANNOTATION_KEYWORDS
            and not keyword.startswith("x-")
        ):
            issues.append(KeywordSupportIssue(keyword, keyword_path))
        if keyword == "format" and isinstance(value, str) and value not in SUPPORTED_FORMATS:
            issues.append(KeywordSupportIssue(f"format:{value}", keyword_path))
        if keyword == "$ref":
            issues.append(KeywordSupportIssue("$ref:unresolved", keyword_path))
        if keyword == "type":
            declared_types = value if isinstance(value, list) else [value]
            for declared_type in declared_types:
                if not isinstance(declared_type, str) or declared_type not in SUPPORTED_TYPES:
                    issues.append(KeywordSupportIssue(f"type:{declared_type}", keyword_path))
        if keyword == "pattern":
            if not isinstance(value, str):
                issues.append(KeywordSupportIssue("pattern:invalid", keyword_path))
            else:
                try:
                    re.compile(value)
                except re.error:
                    issues.append(KeywordSupportIssue("pattern:invalid", keyword_path))

        if keyword in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            for child_name, child_schema in value.items():
                if isinstance(child_schema, (dict, bool)):
                    child_path = _child_path(keyword_path, child_name)
                    _audit_schema(child_schema, child_path, issues)
        elif keyword in _SCHEMA_ARRAY_KEYWORDS and isinstance(value, list):
            for index, child_schema in enumerate(value):
                if isinstance(child_schema, (dict, bool)):
                    _audit_schema(child_schema, f"{keyword_path}[{index}]", issues)
        elif keyword in _SCHEMA_VALUE_KEYWORDS:
            if isinstance(value, list) and keyword == "items":
                for index, child_schema in enumerate(value):
                    if isinstance(child_schema, (dict, bool)):
                        _audit_schema(child_schema, f"{keyword_path}[{index}]", issues)
            elif isinstance(value, (dict, bool)):
                _audit_schema(value, keyword_path, issues)


def _validate(
    instance: Any,
    schema: Schema,
    path: str,
    issues: list[ValidationIssue],
) -> None:
    if schema is True:
        return
    if schema is False:
        issues.append(
            ValidationIssue(
                code="false_schema",
                path=path,
                message="Value is rejected by a false schema.",
                expected="no value",
                actual=instance,
            )
        )
        return

    if "allOf" in schema:
        for branch in _schema_list(schema["allOf"]):
            _validate(instance, branch, path, issues)

    if "anyOf" in schema:
        branches = _schema_list(schema["anyOf"])
        if branches and not any(_is_valid(instance, branch) for branch in branches):
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
        matches = sum(_is_valid(instance, branch) for branch in branches)
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

    if "const" in schema and not _json_equal(instance, schema["const"]):
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
    if isinstance(enum, list) and not any(_json_equal(instance, item) for item in enum):
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

    condition = schema.get("if")
    if isinstance(condition, (dict, bool)):
        branch_name = "then" if _is_valid(instance, condition) else "else"
        conditional_branch: Any = schema.get(branch_name)
        if isinstance(conditional_branch, (dict, bool)):
            _validate(instance, conditional_branch, path, issues)


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
        if isinstance(property_schema, (dict, bool)):
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

    if schema.get("uniqueItems") is True:
        for index, item in enumerate(instance):
            if any(_json_equal(item, previous) for previous in instance[:index]):
                issues.append(
                    ValidationIssue(
                        code="unique_items",
                        path=path,
                        message="Array items must be unique.",
                        expected="unique items",
                        actual=item,
                    )
                )
                break

    prefix_items = schema.get("prefixItems")
    if isinstance(prefix_items, list):
        for index, item_schema in enumerate(prefix_items[: len(instance)]):
            if isinstance(item_schema, (dict, bool)):
                _validate(instance[index], item_schema, f"{path}[{index}]", issues)
        remaining_schema = schema.get("items", True)
        if isinstance(remaining_schema, (dict, bool)):
            for index in range(len(prefix_items), len(instance)):
                _validate(instance[index], remaining_schema, f"{path}[{index}]", issues)
        return

    item_schema = schema.get("items")
    if isinstance(item_schema, list):
        for index, tuple_schema in enumerate(item_schema[: len(instance)]):
            if isinstance(tuple_schema, (dict, bool)):
                _validate(instance[index], tuple_schema, f"{path}[{index}]", issues)
        additional = schema.get("additionalItems", True)
        if isinstance(additional, (dict, bool)):
            for index in range(len(item_schema), len(instance)):
                _validate(instance[index], additional, f"{path}[{index}]", issues)
    elif isinstance(item_schema, (dict, bool)):
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

    pattern = schema.get("pattern")
    if isinstance(pattern, str):
        try:
            matches = re.search(pattern, instance) is not None
        except re.error:
            issues.append(
                ValidationIssue(
                    code="invalid_pattern",
                    path=path,
                    message="Schema pattern is not a valid regular expression.",
                    expected=pattern,
                    actual=instance,
                )
            )
        else:
            if not matches:
                issues.append(
                    ValidationIssue(
                        code="pattern_mismatch",
                        path=path,
                        message="String does not match the required pattern.",
                        expected=pattern,
                        actual=instance,
                    )
                )

    format_name = schema.get("format")
    if isinstance(format_name, str) and not _matches_format(instance, format_name):
        issues.append(
            ValidationIssue(
                code="format_mismatch",
                path=path,
                message=f"String is not a valid {format_name} value.",
                expected=format_name,
                actual=instance,
            )
        )


def _validate_number(
    instance: int | float,
    schema: dict[str, Any],
    path: str,
    issues: list[ValidationIssue],
) -> None:
    bounds = {
        "minimum": "minimum",
        "maximum": "maximum",
        "exclusiveMinimum": "exclusive_minimum",
        "exclusiveMaximum": "exclusive_maximum",
    }
    for keyword, code in bounds.items():
        bound = schema.get(keyword)
        if not isinstance(bound, (int, float)) or isinstance(bound, bool):
            continue
        violates = (
            (keyword == "minimum" and instance < bound)
            or (keyword == "maximum" and instance > bound)
            or (keyword == "exclusiveMinimum" and instance <= bound)
            or (keyword == "exclusiveMaximum" and instance >= bound)
        )
        if violates:
            issues.append(
                ValidationIssue(
                    code=code,
                    path=path,
                    message=f"Numeric value violates {keyword}.",
                    expected=bound,
                    actual=instance,
                )
            )

    divisor = schema.get("multipleOf")
    if isinstance(divisor, (int, float)) and not isinstance(divisor, bool) and divisor > 0:
        quotient = instance / divisor
        if not math.isclose(quotient, round(quotient), rel_tol=1e-9, abs_tol=1e-9):
            issues.append(
                ValidationIssue(
                    code="multiple_of",
                    path=path,
                    message="Numeric value is not a multiple of the required divisor.",
                    expected=divisor,
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


def _matches_format(instance: str, format_name: str) -> bool:
    try:
        if format_name == "date-time":
            date_time_pattern = (
                r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}"
                r"(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
            )
            if re.fullmatch(date_time_pattern, instance) is None:
                return False
            normalized = instance.replace("z", "+00:00").replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            return parsed.tzinfo is not None
        if format_name == "date":
            date.fromisoformat(instance)
            return True
        if format_name == "time":
            time.fromisoformat(instance.replace("Z", "+00:00"))
            return True
        if format_name == "email":
            local, separator, domain = instance.rpartition("@")
            return (
                separator == "@"
                and bool(local)
                and _matches_hostname(domain)
                and not any(character.isspace() for character in instance)
            )
        if format_name == "hostname":
            return _matches_hostname(instance)
        if format_name == "ipv4":
            return isinstance(ipaddress.ip_address(instance), ipaddress.IPv4Address)
        if format_name == "ipv6":
            return isinstance(ipaddress.ip_address(instance), ipaddress.IPv6Address)
        if format_name == "uri":
            return bool(urlsplit(instance).scheme)
        if format_name == "uuid":
            UUID(instance)
            return True
    except ValueError:
        return False
    return False


def _matches_hostname(instance: str) -> bool:
    if not instance or len(instance) > 253:
        return False
    labels = instance.removesuffix(".").split(".")
    label_pattern = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
    return all(label_pattern.fullmatch(label) for label in labels)


def _json_equal(left: Any, right: Any) -> bool:
    if _is_number(left) and _is_number(right):
        return bool(left == right)
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        assert isinstance(right, list)
        return len(left) == len(right) and all(
            _json_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, dict):
        assert isinstance(right, dict)
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return bool(left == right)


def _is_valid(instance: Any, schema: Schema) -> bool:
    issues: list[ValidationIssue] = []
    _validate(instance, schema, "$", issues)
    return not issues


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


def _schema_list(value: Any) -> list[Schema]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, (dict, bool))]


def _child_path(parent: str, key: str) -> str:
    if key.isidentifier():
        return f"{parent}.{key}"
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'{parent}["{escaped}"]'
