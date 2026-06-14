from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from agentcompat.models import ToolCall

MAX_INPUT_BYTES = 10 * 1024 * 1024
_SCHEMA_MAP_KEYWORDS = frozenset({"$defs", "definitions", "properties"})
_SCHEMA_ARRAY_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_VALUE_KEYWORDS = frozenset(
    {"additionalItems", "additionalProperties", "else", "if", "items", "then"}
)


class InputError(ValueError):
    """Raised when an input file does not match the supported data contract."""


def parse_tool_bundle(payload: Any) -> dict[str, dict[str, Any]]:
    raw_tools = payload.get("tools") if isinstance(payload, dict) else payload
    if not isinstance(raw_tools, list):
        raise InputError("Tool bundle must be a list or an object containing a 'tools' list.")

    tools: dict[str, dict[str, Any]] = {}
    for index, raw_tool in enumerate(raw_tools):
        if not isinstance(raw_tool, dict):
            raise InputError(f"Tool at index {index} must be an object.")

        name: Any = raw_tool.get("name")
        schema: Any = raw_tool.get("inputSchema", raw_tool.get("input_schema"))

        function = raw_tool.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            schema = function.get("parameters")

        if not isinstance(name, str) or not name.strip():
            raise InputError(f"Tool at index {index} has no valid name.")
        if not isinstance(schema, dict):
            raise InputError(f"Tool {name!r} has no supported input schema.")
        if name in tools:
            raise InputError(f"Tool bundle contains duplicate name {name!r}.")
        tools[name] = schema

    if not tools:
        raise InputError("Tool bundle contains no tools.")
    return tools


def read_traces(path: Path, *, max_traces: int = 10_000) -> list[ToolCall]:
    _check_file_size(path)
    traces: list[ToolCall] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            if len(traces) >= max_traces:
                raise InputError(f"Trace file exceeds the limit of {max_traces} records.")
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InputError(f"Invalid JSON on line {line_number}: {exc.msg}.") from exc
            traces.append(_parse_trace(payload, line_number))

    if not traces:
        raise InputError("Trace file contains no records.")
    return traces


def load_tool_bundle(path: Path) -> dict[str, dict[str, Any]]:
    document = load_json(path)
    tools = parse_tool_bundle(document)
    resolver = _LocalReferenceResolver(path, document)
    resolved_tools: dict[str, dict[str, Any]] = {}
    for name, schema in tools.items():
        resolved = resolver.resolve(
            schema,
            current_path=path,
            current_document=document,
        )
        resolved_tools[name] = resolved if isinstance(resolved, dict) else {"allOf": [resolved]}
    return resolved_tools


def load_json(path: Path) -> Any:
    _check_file_size(path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise InputError(f"Invalid JSON in {path}: {exc.msg}.") from exc


def _parse_trace(payload: Any, line_number: int) -> ToolCall:
    if not isinstance(payload, dict):
        raise InputError(f"Trace on line {line_number} must be an object.")

    trace_id = payload.get("trace_id")
    tool = payload.get("tool")
    arguments = payload.get("arguments")
    weight = payload.get("weight", 1.0)

    if not isinstance(trace_id, str) or not trace_id.strip():
        raise InputError(f"Trace on line {line_number} has no valid trace_id.")
    if not isinstance(tool, str) or not tool.strip():
        raise InputError(f"Trace {trace_id!r} has no valid tool name.")
    if not isinstance(arguments, dict):
        raise InputError(f"Trace {trace_id!r} arguments must be an object.")
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
        raise InputError(f"Trace {trace_id!r} weight must be a positive number.")

    return ToolCall(trace_id, tool, arguments, float(weight))


def _check_file_size(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InputError(f"Cannot read {path}: {exc}.") from exc
    if size > MAX_INPUT_BYTES:
        raise InputError(f"Input file {path} exceeds the {MAX_INPUT_BYTES}-byte limit.")


class _LocalReferenceResolver:
    def __init__(self, root_path: Path, root_document: Any) -> None:
        self.root = root_path.resolve().parent
        self.documents = {root_path.resolve(): root_document}

    def resolve(
        self,
        schema: dict[str, Any] | bool,
        *,
        current_path: Path,
        current_document: Any,
        stack: tuple[tuple[Path, str], ...] = (),
    ) -> dict[str, Any] | bool:
        if isinstance(schema, bool):
            return schema

        reference = schema.get("$ref")
        if reference is not None:
            if not isinstance(reference, str):
                raise InputError("$ref values must be strings.")
            target_path, fragment, target_document = self._resolve_target(
                reference,
                current_path,
                current_document,
            )
            key = (target_path, fragment)
            if key in stack:
                raise InputError(f"Local $ref cycle detected at {reference!r}.")
            target = self._resolve_pointer(target_document, fragment, reference)
            if not isinstance(target, (dict, bool)):
                raise InputError(f"$ref {reference!r} does not point to a schema.")
            resolved = self.resolve(
                target,
                current_path=target_path,
                current_document=target_document,
                stack=(*stack, key),
            )
            siblings = {key: value for key, value in schema.items() if key != "$ref"}
            if not siblings:
                return resolved
            resolved_siblings = self._resolve_children(
                siblings,
                current_path=current_path,
                current_document=current_document,
                stack=stack,
            )
            return {"allOf": [resolved], **resolved_siblings}

        return self._resolve_children(
            schema,
            current_path=current_path,
            current_document=current_document,
            stack=stack,
        )

    def _resolve_children(
        self,
        schema: dict[str, Any],
        *,
        current_path: Path,
        current_document: Any,
        stack: tuple[tuple[Path, str], ...],
    ) -> dict[str, Any]:
        resolved = dict(schema)
        for keyword, value in schema.items():
            if keyword in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
                resolved[keyword] = {
                    name: self.resolve(
                        child,
                        current_path=current_path,
                        current_document=current_document,
                        stack=stack,
                    )
                    if isinstance(child, (dict, bool))
                    else child
                    for name, child in value.items()
                }
            elif keyword in _SCHEMA_ARRAY_KEYWORDS and isinstance(value, list):
                resolved[keyword] = [
                    self.resolve(
                        child,
                        current_path=current_path,
                        current_document=current_document,
                        stack=stack,
                    )
                    if isinstance(child, (dict, bool))
                    else child
                    for child in value
                ]
            elif keyword in _SCHEMA_VALUE_KEYWORDS:
                if isinstance(value, list) and keyword == "items":
                    resolved[keyword] = [
                        self.resolve(
                            child,
                            current_path=current_path,
                            current_document=current_document,
                            stack=stack,
                        )
                        if isinstance(child, (dict, bool))
                        else child
                        for child in value
                    ]
                elif isinstance(value, (dict, bool)):
                    resolved[keyword] = self.resolve(
                        value,
                        current_path=current_path,
                        current_document=current_document,
                        stack=stack,
                    )
        return resolved

    def _resolve_target(
        self,
        reference: str,
        current_path: Path,
        current_document: Any,
    ) -> tuple[Path, str, Any]:
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc or parsed.query:
            raise InputError(f"Only local file $ref values are supported: {reference!r}.")

        fragment = unquote(parsed.fragment)
        if not parsed.path:
            return current_path.resolve(), fragment, current_document

        target_path = (current_path.resolve().parent / unquote(parsed.path)).resolve()
        try:
            target_path.relative_to(self.root)
        except ValueError as exc:
            raise InputError(
                f"Local $ref {reference!r} resolves outside schema root {self.root}."
            ) from exc

        if target_path not in self.documents:
            self.documents[target_path] = load_json(target_path)
        return target_path, fragment, self.documents[target_path]

    @staticmethod
    def _resolve_pointer(document: Any, fragment: str, reference: str) -> Any:
        if not fragment:
            return document
        if not fragment.startswith("/"):
            raise InputError(f"$ref {reference!r} uses an unsupported non-pointer fragment.")

        current = document
        for raw_token in fragment[1:].split("/"):
            token = raw_token.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict) and token in current:
                current = current[token]
            elif isinstance(current, list) and token.isdigit():
                index = int(token)
                if index >= len(current):
                    raise InputError(f"$ref {reference!r} points outside an array.")
                current = current[index]
            else:
                raise InputError(f"$ref {reference!r} points to a missing location.")
        return current
