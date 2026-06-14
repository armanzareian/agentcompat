from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentcompat.models import ToolCall

MAX_INPUT_BYTES = 10 * 1024 * 1024


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
    return parse_tool_bundle(load_json(path))


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
    if (
        isinstance(weight, bool)
        or not isinstance(weight, (int, float))
        or weight <= 0
    ):
        raise InputError(f"Trace {trace_id!r} weight must be a positive number.")

    return ToolCall(trace_id, tool, arguments, float(weight))


def _check_file_size(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise InputError(f"Cannot read {path}: {exc}.") from exc
    if size > MAX_INPUT_BYTES:
        raise InputError(f"Input file {path} exceeds the {MAX_INPUT_BYTES}-byte limit.")
