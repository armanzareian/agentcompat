from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from agentcompat.models import ToolCall

MAX_INPUT_BYTES = 10 * 1024 * 1024
TRACE_FORMATS = frozenset({"canonical", "openai", "anthropic", "mcp", "langchain"})
_SCHEMA_MAP_KEYWORDS = frozenset({"$defs", "definitions", "properties"})
_SCHEMA_ARRAY_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_VALUE_KEYWORDS = frozenset(
    {"additionalItems", "additionalProperties", "else", "if", "items", "then"}
)


class InputError(ValueError):
    """Raised when an input file does not match the supported data contract."""


@dataclass(frozen=True, slots=True)
class RedactionConfig:
    paths: tuple[str, ...] = ()
    key_patterns: tuple[str, ...] = ()
    replacement: str = "[REDACTED]"


@dataclass(frozen=True, slots=True)
class _PreparedRedaction:
    paths: frozenset[str]
    key_patterns: tuple[re.Pattern[str], ...]
    replacement: str


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


def read_traces(
    path: Path,
    *,
    max_traces: int = 10_000,
    trace_format: str = "canonical",
    redaction: RedactionConfig | None = None,
) -> list[ToolCall]:
    if trace_format not in TRACE_FORMATS:
        supported = ", ".join(sorted(TRACE_FORMATS))
        raise InputError(f"Unsupported trace format {trace_format!r}; choose one of {supported}.")

    _check_file_size(path)
    prepared_redaction = _prepare_redaction(redaction)
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
            for trace in _iter_trace_records(payload, line_number, trace_format):
                if len(traces) >= max_traces:
                    raise InputError(f"Trace file exceeds the limit of {max_traces} records.")
                traces.append(_apply_redaction(trace, prepared_redaction))

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


def _iter_trace_records(payload: Any, line_number: int, trace_format: str) -> Iterator[ToolCall]:
    if trace_format == "canonical":
        yield _parse_trace(payload, line_number)
    elif trace_format == "openai":
        yield from _iter_openai_traces(payload, line_number)
    elif trace_format == "anthropic":
        yield from _iter_anthropic_traces(payload, line_number)
    elif trace_format == "mcp":
        yield from _iter_mcp_traces(payload, line_number)
    elif trace_format == "langchain":
        yield from _iter_langchain_traces(payload, line_number)


def _iter_openai_traces(payload: Any, line_number: int) -> Iterator[ToolCall]:
    if not isinstance(payload, dict):
        raise InputError(f"OpenAI trace on line {line_number} must be an object.")

    weight = _read_weight(payload, f"line {line_number}")
    event_item = payload.get("item")
    if isinstance(event_item, dict) and event_item.get("type") == "function_call":
        yield _parse_openai_call(
            event_item,
            line_number,
            "item",
            weight,
            fallback_index=0,
        )
        return

    output = payload.get("output")
    if isinstance(output, list):
        for index, item in enumerate(output):
            if isinstance(item, dict) and item.get("type") == "function_call":
                yield _parse_openai_call(
                    item,
                    line_number,
                    f"output[{index}]",
                    weight,
                    fallback_index=index,
                )
        return

    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice_index, choice in enumerate(choices):
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            tool_calls = message.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for call_index, tool_call in enumerate(tool_calls):
                if isinstance(tool_call, dict):
                    yield _parse_openai_chat_tool_call(
                        tool_call,
                        line_number,
                        f"choices[{choice_index}].message.tool_calls[{call_index}]",
                        weight,
                        fallback_index=call_index,
                    )
        return

    return


def _parse_openai_call(
    item: dict[str, Any],
    line_number: int,
    source: str,
    weight: float,
    *,
    fallback_index: int,
) -> ToolCall:
    tool = _require_string(item.get("name"), f"line {line_number} {source}.name")
    arguments = _arguments_object(
        item.get("arguments"),
        f"line {line_number} {source}.arguments",
        allow_json_string=True,
    )
    trace_id = _optional_string(item.get("call_id")) or _optional_string(item.get("id"))
    if trace_id is None:
        trace_id = f"line-{line_number}-openai-{fallback_index}"
    return ToolCall(trace_id, tool, arguments, weight)


def _parse_openai_chat_tool_call(
    tool_call: dict[str, Any],
    line_number: int,
    source: str,
    weight: float,
    *,
    fallback_index: int,
) -> ToolCall:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        raise InputError(f"OpenAI trace {source} on line {line_number} has no function object.")
    tool = _require_string(function.get("name"), f"line {line_number} {source}.function.name")
    arguments = _arguments_object(
        function.get("arguments"),
        f"line {line_number} {source}.function.arguments",
        allow_json_string=True,
    )
    trace_id = _optional_string(tool_call.get("id"))
    if trace_id is None:
        trace_id = f"line-{line_number}-openai-{fallback_index}"
    return ToolCall(trace_id, tool, arguments, weight)


def _iter_anthropic_traces(payload: Any, line_number: int) -> Iterator[ToolCall]:
    if not isinstance(payload, dict):
        raise InputError(f"Anthropic trace on line {line_number} must be an object.")

    weight = _read_weight(payload, f"line {line_number}")
    block = payload.get("content_block")
    if isinstance(block, dict) and block.get("type") == "tool_use":
        yield _parse_anthropic_tool_block(
            block,
            line_number,
            "content_block",
            weight,
            fallback_index=0,
        )
        return

    content = payload.get("content")
    if isinstance(content, list):
        for index, item in enumerate(content):
            if isinstance(item, dict) and item.get("type") == "tool_use":
                yield _parse_anthropic_tool_block(
                    item,
                    line_number,
                    f"content[{index}]",
                    weight,
                    fallback_index=index,
                )
        return

    if payload.get("type") == "tool_use":
        yield _parse_anthropic_tool_block(payload, line_number, "$", weight, fallback_index=0)
        return

    return


def _parse_anthropic_tool_block(
    block: dict[str, Any],
    line_number: int,
    source: str,
    weight: float,
    *,
    fallback_index: int,
) -> ToolCall:
    tool = _require_string(block.get("name"), f"line {line_number} {source}.name")
    arguments = _arguments_object(
        block.get("input"),
        f"line {line_number} {source}.input",
        allow_json_string=False,
    )
    trace_id = _optional_string(block.get("id"))
    if trace_id is None:
        trace_id = f"line-{line_number}-anthropic-{fallback_index}"
    return ToolCall(trace_id, tool, arguments, weight)


def _iter_mcp_traces(payload: Any, line_number: int) -> Iterator[ToolCall]:
    if not isinstance(payload, dict):
        raise InputError(f"MCP trace on line {line_number} must be an object.")

    outer_weight = _read_weight(payload, f"line {line_number}")
    request = payload.get("request")
    if isinstance(request, dict):
        payload = request

    if payload.get("method") != "tools/call":
        return

    params = payload.get("params")
    if not isinstance(params, dict):
        raise InputError(f"MCP trace on line {line_number} params must be an object.")
    tool = _require_string(params.get("name"), f"line {line_number} params.name")
    arguments = _arguments_object(
        params.get("arguments", {}),
        f"line {line_number} params.arguments",
        allow_json_string=False,
    )
    weight = _read_weight(payload, f"line {line_number}") if "weight" in payload else outer_weight
    trace_id = _optional_string(payload.get("id"))
    if trace_id is None:
        trace_id = f"line-{line_number}-mcp-0"
    yield ToolCall(trace_id, tool, arguments, weight)


def _iter_langchain_traces(payload: Any, line_number: int) -> Iterator[ToolCall]:
    if not isinstance(payload, dict):
        raise InputError(f"LangChain trace on line {line_number} must be an object.")
    if payload.get("event") != "on_tool_start":
        return

    data = payload.get("data")
    if not isinstance(data, dict):
        raise InputError(f"LangChain trace on line {line_number} data must be an object.")
    tool = _require_string(
        payload.get("name", data.get("name")),
        f"line {line_number} name",
    )
    arguments = _arguments_object(
        data.get("input", data.get("inputs", {})),
        f"line {line_number} data.input",
        allow_json_string=False,
    )
    weight = _read_weight(payload, f"line {line_number}")
    trace_id = _optional_string(payload.get("run_id")) or f"line-{line_number}-langchain-0"
    yield ToolCall(trace_id, tool, arguments, weight)


def _read_weight(payload: dict[str, Any], source: str) -> float:
    weight = payload.get("weight", 1.0)
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
        raise InputError(f"Trace weight at {source} must be a positive number.")
    return float(weight)


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _require_string(value: Any, source: str) -> str:
    result = _optional_string(value)
    if result is None:
        raise InputError(f"Required string field {source} is missing.")
    return result


def _arguments_object(value: Any, source: str, *, allow_json_string: bool) -> dict[str, Any]:
    if isinstance(value, str) and allow_json_string:
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise InputError(f"Invalid JSON object at {source}: {exc.msg}.") from exc
    if not isinstance(value, dict):
        raise InputError(f"Arguments at {source} must be an object.")
    return value


def _prepare_redaction(redaction: RedactionConfig | None) -> _PreparedRedaction | None:
    if redaction is None or (not redaction.paths and not redaction.key_patterns):
        return None
    return _PreparedRedaction(
        frozenset(redaction.paths),
        _compile_redaction_patterns(redaction.key_patterns),
        redaction.replacement,
    )


def _apply_redaction(trace: ToolCall, redaction: _PreparedRedaction | None) -> ToolCall:
    if redaction is None:
        return trace
    arguments = _redact_value(
        trace.arguments,
        path="$",
        wildcard_path="$",
        exact_paths=redaction.paths,
        key_patterns=redaction.key_patterns,
        replacement=redaction.replacement,
    )
    if not isinstance(arguments, dict):
        raise InputError("Redaction paths cannot replace the root arguments object.")
    return ToolCall(trace.trace_id, trace.tool, arguments, trace.weight)


def _compile_redaction_patterns(raw_patterns: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    compiled: list[re.Pattern[str]] = []
    for index, raw_pattern in enumerate(raw_patterns, start=1):
        try:
            compiled.append(re.compile(raw_pattern))
        except re.error as exc:
            raise InputError(f"Invalid redaction key pattern #{index}: {exc.msg}.") from exc
    return tuple(compiled)


def _redact_value(
    value: Any,
    *,
    path: str,
    wildcard_path: str,
    exact_paths: frozenset[str],
    key_patterns: tuple[re.Pattern[str], ...],
    replacement: str,
) -> Any:
    if path != "$" and (path in exact_paths or wildcard_path in exact_paths):
        return replacement
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            key_path = _join_object_path(path, key)
            key_wildcard_path = _join_object_path(wildcard_path, key)
            if any(pattern.search(key) for pattern in key_patterns):
                redacted[key] = replacement
            else:
                redacted[key] = _redact_value(
                    child,
                    path=key_path,
                    wildcard_path=key_wildcard_path,
                    exact_paths=exact_paths,
                    key_patterns=key_patterns,
                    replacement=replacement,
                )
        return redacted
    if isinstance(value, list):
        return [
            _redact_value(
                child,
                path=f"{path}[{index}]",
                wildcard_path=f"{wildcard_path}[*]",
                exact_paths=exact_paths,
                key_patterns=key_patterns,
                replacement=replacement,
            )
            for index, child in enumerate(value)
        ]
    return value


def _join_object_path(parent: str, key: str) -> str:
    if key.isidentifier():
        return f"{parent}.{key}"
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'{parent}["{escaped}"]'


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
