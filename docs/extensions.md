# Extension API

AgentCompat exposes typed Python extension points for applications that already collect traces or
store tool schemas in a custom format. Extensions are programmatic only: the CLI keeps its fixed
offline input formats and performs no dynamic imports.

## Trace Adapters

A trace adapter converts one parsed JSON Lines record into zero or more canonical `ToolCall`
objects. Register adapters with `ExtensionRegistry.with_trace_adapter()` and pass the registry to
`read_traces()` or `iter_traces()`:

```python
from collections.abc import Iterable
from pathlib import Path

from agentcompat import ExtensionRegistry, ToolCall
from agentcompat.io import read_traces


def acme_adapter(payload: object, line_number: int) -> Iterable[ToolCall]:
    if not isinstance(payload, dict) or payload.get("kind") != "tool":
        return ()
    return (
        ToolCall(
            trace_id=f"line-{line_number}-acme",
            tool=str(payload["name"]),
            arguments=dict(payload["arguments"]),
            weight=float(payload.get("weight", 1.0)),
        ),
    )


extensions = ExtensionRegistry().with_trace_adapter("acme", acme_adapter)
traces = read_traces(Path("acme-traces.jsonl"), trace_format="acme", extensions=extensions)
```

Built-in redaction still runs after the adapter returns and before replay receives the canonical
tool call. Adapter names are explicit; built-in formats are `canonical`, `openai`, `anthropic`,
`mcp`, and `langchain`.

## Schema Sources

A schema source loads a normalized map of tool names to JSON Schema objects. This is useful when
schemas are stored in a registry export, derived from SDK metadata, or embedded in another
manifest:

```python
from pathlib import Path

from agentcompat import ExtensionRegistry, ToolBundle
from agentcompat.io import load_tool_bundle


def acme_schema_source(path: Path) -> ToolBundle:
    del path
    return {
        "search_orders": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
        }
    }


extensions = ExtensionRegistry().with_schema_source("acme", acme_schema_source)
baseline = load_tool_bundle(Path("baseline.acme"), schema_source="acme", extensions=extensions)
```

Custom schema sources return already-normalized schemas. AgentCompat still audits unsupported JSON
Schema semantics before analysis, so unsupported keywords are rejected rather than silently scored.

## Typed Contract

The package includes `py.typed` and exports:

- `ExtensionRegistry`
- `TraceAdapter`
- `SchemaSource`
- `ToolBundle`
- `ToolCall`

Use these names in downstream annotations to keep extension code compatible with strict type
checking.
