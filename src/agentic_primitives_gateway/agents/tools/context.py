"""Request-scoped sideband between tool handlers and the runner.

Tool handlers return a plain string to the LLM — that's the ``output``
that ends up on ``ToolArtifact.output``.  But some tools also have a
richer, UI-only payload they want to surface (e.g. ``knowledge_search``
with ``include_sources=True`` emits structured citations that shouldn't
cost LLM tokens).  Rather than change the handler signature to a tuple
return (which would cascade through every handler + ``execute_tool`` +
the ``ToolDefinition`` type), handlers write the structured payload to
this contextvar.  The runner reads + resets it around each tool call
when building the ``ToolArtifact``.

The contextvar is scoped to a single tool call, not a whole run: the
runner resets it after capturing the value so the next tool call starts
with a clean slate.  This matches the per-primitive contextvar pattern
used elsewhere in the codebase (``primitives/<p>/context.py``).
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_current_artifact_structured: ContextVar[dict[str, Any] | None] = ContextVar(
    "current_artifact_structured", default=None
)


def set_current_artifact_structured(value: dict[str, Any] | None) -> None:
    """Set the structured sideband payload for the current tool call."""
    _current_artifact_structured.set(value)


def pop_current_artifact_structured() -> dict[str, Any] | None:
    """Read and reset the current tool's structured sideband payload.

    Called by the runner immediately after each tool handler returns
    so the next tool call starts with a fresh ``None``.  Never raises.
    """
    value = _current_artifact_structured.get()
    _current_artifact_structured.set(None)
    return value
