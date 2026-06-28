"""Trace-driven compatibility testing for LLM tool schemas."""

from agentcompat.extensions import ExtensionRegistry, SchemaSource, ToolBundle, TraceAdapter
from agentcompat.models import ToolCall

__version__ = "0.1.0"

__all__ = [
    "ExtensionRegistry",
    "SchemaSource",
    "ToolBundle",
    "ToolCall",
    "TraceAdapter",
    "__version__",
]
