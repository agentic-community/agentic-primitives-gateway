"""Tools-specific audit wrappers used by ``ToolsProvider.__init_subclass__``.

Every subclass gets ``invoke_tool`` / ``register_tool`` / ``delete_tool``
/ ``register_server`` wrapped automatically to emit the matching
``tool.*`` action (per ``AuditAction``) on success and failure — so an
agent-tool caller invoking the tools primitive produces the same specific
event the REST path already emits via ``audit_mutation``.

Kept as a separate module so the ABC stays dependency-light: the audit
subsystem is imported lazily when subclasses are defined, not when
``ToolsProvider`` itself is imported (it's imported early during app
bootstrap).
"""

from __future__ import annotations

import functools
from typing import Any

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType


def _emit(action: str, outcome: AuditOutcome, *, resource_id: str | None, metadata: dict[str, Any]) -> None:
    # ``layer="primitive"`` lets operators distinguish this provider-
    # boundary event from the route-layer ``audit_mutation`` event that
    # carries the same ``action`` + ``request_id``.  Route-layer events
    # have ``http_method``/``http_path``/``http_status`` populated;
    # provider-boundary events carry ``layer=primitive``.  Either can
    # be filtered via the router's ``sample_rates``/``exclude_actions``
    # with a metadata-aware query in downstream dashboards.
    metadata.setdefault("layer", "primitive")
    emit_audit_event(
        action=action,
        outcome=outcome,
        resource_type=ResourceType.TOOL,
        resource_id=resource_id,
        metadata=metadata,
    )


def wrap_invoke_tool(func: Any) -> Any:
    """Wrap ``invoke_tool`` to emit a provider-level ``tool.call`` audit event.

    Distinct from the agent-tool-layer ``tool.call`` emitted by
    ``agents/tools/catalog.execute_tool`` — that one covers the
    agent-tool name (``memory_search``, ``call_researcher``, …); this
    one covers the concrete backend tool the primitive invokes (MCP
    server tool name).  Metadata marks the layer so dashboards can
    separate them if needed.
    """

    @functools.wraps(func)
    async def wrapper(self: Any, tool_name: str, params: dict[str, Any]) -> Any:
        try:
            result = await func(self, tool_name, params)
        except Exception as exc:
            _emit(
                AuditAction.TOOL_CALL,
                AuditOutcome.ERROR,
                resource_id=tool_name,
                metadata={"tool_name": tool_name, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.TOOL_CALL,
            AuditOutcome.SUCCESS,
            resource_id=tool_name,
            metadata={"tool_name": tool_name},
        )
        return result

    return wrapper


def wrap_register_tool(func: Any) -> Any:
    """Wrap ``register_tool`` to emit ``tool.register``."""

    @functools.wraps(func)
    async def wrapper(self: Any, tool_def: dict[str, Any]) -> Any:
        tool_name = tool_def.get("name") if isinstance(tool_def, dict) else None
        try:
            result = await func(self, tool_def)
        except Exception as exc:
            _emit(
                AuditAction.TOOL_REGISTER,
                AuditOutcome.ERROR,
                resource_id=tool_name,
                metadata={"tool_name": tool_name, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.TOOL_REGISTER,
            AuditOutcome.SUCCESS,
            resource_id=tool_name,
            metadata={"tool_name": tool_name},
        )
        return result

    return wrapper


def wrap_delete_tool(func: Any) -> Any:
    """Wrap ``delete_tool`` to emit ``tool.delete``."""

    @functools.wraps(func)
    async def wrapper(self: Any, tool_name: str) -> Any:
        try:
            result = await func(self, tool_name)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.TOOL_DELETE,
                AuditOutcome.ERROR,
                resource_id=tool_name,
                metadata={"tool_name": tool_name, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.TOOL_DELETE,
            AuditOutcome.SUCCESS,
            resource_id=tool_name,
            metadata={"tool_name": tool_name},
        )
        return result

    return wrapper


def wrap_register_server(func: Any) -> Any:
    """Wrap ``register_server`` to emit ``tool.server.register``."""

    @functools.wraps(func)
    async def wrapper(self: Any, server_config: dict[str, Any]) -> Any:
        server_name = server_config.get("name") if isinstance(server_config, dict) else None
        try:
            result = await func(self, server_config)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.TOOL_SERVER_REGISTER,
                AuditOutcome.ERROR,
                resource_id=server_name,
                metadata={"server_name": server_name, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.TOOL_SERVER_REGISTER,
            AuditOutcome.SUCCESS,
            resource_id=server_name,
            metadata={"server_name": server_name},
        )
        return result

    return wrapper
