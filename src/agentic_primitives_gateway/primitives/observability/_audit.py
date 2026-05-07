"""Observability audit wrappers used by ``ObservabilityProvider.__init_subclass__``.

Every subclass gets ``ingest_trace`` / ``ingest_log`` / ``update_trace`` /
``score_trace`` / ``log_generation`` / ``flush`` wrapped automatically
to emit the matching ``observability.*`` action on success and failure.
``query_traces`` / ``get_trace`` / ``list_scores`` / session reads stay
on the generic ``provider.call`` event.

Trace bodies + log payloads are not copied into metadata — the trace
system already stores them and an audit copy would duplicate cost +
sensitive fields.  Only identifiers and sizes land in the event.
"""

from __future__ import annotations

import functools
from typing import Any

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType


def _emit(action: str, outcome: AuditOutcome, *, resource_id: str | None, metadata: dict[str, Any]) -> None:
    # See tools/_audit.py::_emit for the ``layer`` rationale.  Note
    # that ``observability.trace.ingest`` and ``observability.log.ingest``
    # are high-volume actions in a busy deployment (one per LLM call +
    # one per request) — operators should consider sampling them via
    # ``audit.filter.sample_rates`` alongside ``provider.call``.
    metadata.setdefault("layer", "primitive")
    emit_audit_event(
        action=action,
        outcome=outcome,
        resource_type=ResourceType.TRACE,
        resource_id=resource_id,
        metadata=metadata,
    )


def wrap_ingest_trace(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, trace: dict[str, Any]) -> Any:
        trace_id = trace.get("trace_id") or trace.get("id") if isinstance(trace, dict) else None
        try:
            result = await func(self, trace)
        except Exception as exc:
            _emit(
                AuditAction.TRACE_INGEST,
                AuditOutcome.ERROR,
                resource_id=trace_id,
                metadata={"trace_id": trace_id, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.TRACE_INGEST,
            AuditOutcome.SUCCESS,
            resource_id=trace_id,
            metadata={"trace_id": trace_id},
        )
        return result

    return wrapper


def wrap_ingest_log(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, log_entry: dict[str, Any]) -> Any:
        level = log_entry.get("level") if isinstance(log_entry, dict) else None
        try:
            result = await func(self, log_entry)
        except Exception as exc:
            _emit(
                AuditAction.LOG_INGEST,
                AuditOutcome.ERROR,
                resource_id=None,
                metadata={"level": level, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.LOG_INGEST,
            AuditOutcome.SUCCESS,
            resource_id=None,
            metadata={"level": level},
        )
        return result

    return wrapper


def wrap_update_trace(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        trace_id: str,
        *,
        name: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> Any:
        try:
            result = await func(
                self,
                trace_id,
                name=name,
                user_id=user_id,
                session_id=session_id,
                input=input,
                output=output,
                metadata=metadata,
                tags=tags,
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.TRACE_UPDATE,
                AuditOutcome.ERROR,
                resource_id=trace_id,
                metadata={"trace_id": trace_id, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.TRACE_UPDATE,
            AuditOutcome.SUCCESS,
            resource_id=trace_id,
            metadata={"trace_id": trace_id, "session_id": session_id},
        )
        return result

    return wrapper


def wrap_score_trace(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        trace_id: str,
        name: str,
        value: float,
        *,
        comment: str | None = None,
        data_type: str | None = None,
    ) -> Any:
        try:
            result = await func(self, trace_id, name, value, comment=comment, data_type=data_type)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.TRACE_SCORE_CREATE,
                AuditOutcome.ERROR,
                resource_id=trace_id,
                metadata={"trace_id": trace_id, "score_name": name, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.TRACE_SCORE_CREATE,
            AuditOutcome.SUCCESS,
            resource_id=trace_id,
            metadata={"trace_id": trace_id, "score_name": name},
        )
        return result

    return wrapper


def wrap_log_generation(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        trace_id: str,
        name: str,
        model: str,
        input: Any = None,
        output: Any = None,
        *,
        usage: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
    ) -> Any:
        try:
            result = await func(
                self,
                trace_id,
                name,
                model,
                input,
                output,
                usage=usage,
                metadata=metadata,
                level=level,
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.TRACE_GENERATION_LOG,
                AuditOutcome.ERROR,
                resource_id=trace_id,
                metadata={
                    "trace_id": trace_id,
                    "name": name,
                    "model": model,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        _emit(
            AuditAction.TRACE_GENERATION_LOG,
            AuditOutcome.SUCCESS,
            resource_id=trace_id,
            metadata={"trace_id": trace_id, "name": name, "model": model},
        )
        return result

    return wrapper


def wrap_flush(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any) -> Any:
        try:
            result = await func(self)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.OBSERVABILITY_FLUSH,
                AuditOutcome.ERROR,
                resource_id=None,
                metadata={"error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.OBSERVABILITY_FLUSH,
            AuditOutcome.SUCCESS,
            resource_id=None,
            metadata={},
        )
        return result

    return wrapper
