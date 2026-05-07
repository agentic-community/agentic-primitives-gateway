"""Browser-specific audit wrappers used by ``BrowserProvider.__init_subclass__``.

Every subclass gets ``navigate`` / ``click`` / ``type_text`` /
``evaluate`` wrapped automatically to emit the matching ``browser.*``
action (per ``AuditAction``) on success and failure, so an agent-tool
caller invoking the browser primitive produces the same specific event
the REST path already emits.

Read-only ops (``screenshot``, ``get_page_content``, ``get_live_view_url``)
stay on the generic ``provider.call`` event from ``MetricsProxy``.
"""

from __future__ import annotations

import functools
from typing import Any

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType


def _emit(action: str, outcome: AuditOutcome, *, resource_id: str | None, metadata: dict[str, Any]) -> None:
    # See tools/_audit.py::_emit for the ``layer`` rationale.
    metadata.setdefault("layer", "primitive")
    emit_audit_event(
        action=action,
        outcome=outcome,
        resource_type=ResourceType.PAGE,
        resource_id=resource_id,
        metadata=metadata,
    )


def wrap_navigate(func: Any) -> Any:
    """Wrap ``navigate`` to emit ``browser.navigate``."""

    @functools.wraps(func)
    async def wrapper(self: Any, session_id: str, url: str) -> Any:
        try:
            result = await func(self, session_id, url)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.BROWSER_NAVIGATE,
                AuditOutcome.ERROR,
                resource_id=session_id,
                metadata={"session_id": session_id, "url": url, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.BROWSER_NAVIGATE,
            AuditOutcome.SUCCESS,
            resource_id=session_id,
            metadata={"session_id": session_id, "url": url},
        )
        return result

    return wrapper


def wrap_click(func: Any) -> Any:
    """Wrap ``click`` to emit ``browser.click``."""

    @functools.wraps(func)
    async def wrapper(self: Any, session_id: str, selector: str) -> Any:
        try:
            result = await func(self, session_id, selector)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.BROWSER_CLICK,
                AuditOutcome.ERROR,
                resource_id=session_id,
                metadata={"session_id": session_id, "selector": selector, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.BROWSER_CLICK,
            AuditOutcome.SUCCESS,
            resource_id=session_id,
            metadata={"session_id": session_id, "selector": selector},
        )
        return result

    return wrapper


def wrap_type_text(func: Any) -> Any:
    """Wrap ``type_text`` to emit ``browser.type``.

    The typed text itself is deliberately not emitted — treat keystroke
    content as sensitive (passwords, PII).  Only the selector and text
    length land in metadata.
    """

    @functools.wraps(func)
    async def wrapper(self: Any, session_id: str, selector: str, text: str) -> Any:
        try:
            result = await func(self, session_id, selector, text)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.BROWSER_TYPE,
                AuditOutcome.ERROR,
                resource_id=session_id,
                metadata={
                    "session_id": session_id,
                    "selector": selector,
                    "text_length": len(text) if text is not None else 0,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        _emit(
            AuditAction.BROWSER_TYPE,
            AuditOutcome.SUCCESS,
            resource_id=session_id,
            metadata={
                "session_id": session_id,
                "selector": selector,
                "text_length": len(text) if text is not None else 0,
            },
        )
        return result

    return wrapper


def wrap_evaluate(func: Any) -> Any:
    """Wrap ``evaluate`` to emit ``browser.evaluate``.

    JS expressions can contain sensitive data so only the expression
    length lands in metadata.
    """

    @functools.wraps(func)
    async def wrapper(self: Any, session_id: str, expression: str) -> Any:
        try:
            result = await func(self, session_id, expression)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.BROWSER_EVALUATE,
                AuditOutcome.ERROR,
                resource_id=session_id,
                metadata={
                    "session_id": session_id,
                    "expression_length": len(expression) if expression is not None else 0,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        _emit(
            AuditAction.BROWSER_EVALUATE,
            AuditOutcome.SUCCESS,
            resource_id=session_id,
            metadata={
                "session_id": session_id,
                "expression_length": len(expression) if expression is not None else 0,
            },
        )
        return result

    return wrapper
