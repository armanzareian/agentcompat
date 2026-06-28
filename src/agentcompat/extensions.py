from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from agentcompat.models import ToolCall

JsonObject = dict[str, Any]
ToolBundle = dict[str, JsonObject]


class TraceAdapter(Protocol):
    """Convert one provider record into zero or more canonical tool calls."""

    def __call__(self, payload: object, line_number: int) -> Iterable[ToolCall]: ...


class SchemaSource(Protocol):
    """Load a normalized tool-bundle map from an application-specific source."""

    def __call__(self, path: Path) -> ToolBundle: ...


@dataclass(frozen=True, slots=True)
class ExtensionRegistry:
    trace_adapters: Mapping[str, TraceAdapter] = field(default_factory=dict)
    schema_sources: Mapping[str, SchemaSource] = field(default_factory=dict)

    def __post_init__(self) -> None:
        trace_adapters = {
            _extension_name(name, "trace adapter"): adapter
            for name, adapter in self.trace_adapters.items()
        }
        schema_sources = {
            _extension_name(name, "schema source"): source
            for name, source in self.schema_sources.items()
        }
        object.__setattr__(self, "trace_adapters", MappingProxyType(trace_adapters))
        object.__setattr__(self, "schema_sources", MappingProxyType(schema_sources))

    @property
    def trace_adapter_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.trace_adapters))

    @property
    def schema_source_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.schema_sources))

    def with_trace_adapter(self, name: str, adapter: TraceAdapter) -> ExtensionRegistry:
        return ExtensionRegistry(
            trace_adapters={**self.trace_adapters, _extension_name(name, "trace adapter"): adapter},
            schema_sources=self.schema_sources,
        )

    def with_schema_source(self, name: str, source: SchemaSource) -> ExtensionRegistry:
        return ExtensionRegistry(
            trace_adapters=self.trace_adapters,
            schema_sources={**self.schema_sources, _extension_name(name, "schema source"): source},
        )


def _extension_name(name: str, label: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{label} name must be a non-empty string.")
    normalized = name.strip()
    if any(character.isspace() for character in normalized):
        raise ValueError(f"{label} name must not contain whitespace.")
    return normalized
