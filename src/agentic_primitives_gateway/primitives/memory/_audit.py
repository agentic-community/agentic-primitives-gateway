"""Memory-specific audit + metadata-scrubbing wrappers used by ``MemoryProvider.__init_subclass__``.

This module does two things, both via ``__init_subclass__``:

1. **Read-path metadata scrub.** ``retrieve`` / ``search`` /
   ``list_memories`` are wrapped to strip operator-configured denylist
   keys from ``MemoryRecord.metadata`` before any caller — REST, agent
   tools, programmatic — sees them.
2. **Write-path specific audit events.** ``store`` / ``delete`` /
   ``create_event`` / ``delete_event`` / ``fork_conversation`` /
   ``create_memory_resource`` / ``delete_memory_resource`` /
   ``add_strategy`` / ``delete_strategy`` emit the matching
   ``memory.*`` action (per ``AuditAction``) on success and failure,
   so an agent-tool invocation produces the same specific event as a
   REST call (which already emits via ``audit_mutation``).

**Why just the read path for scrubbing?** ``store()`` is the write
side — the metadata dict the operator supplies there is what we're
trying to filter on the way *out*.  Scrubbing on write would delete
fields from the operator's own stored records, which is the opposite
of the feature.  Denylist semantics are "never expose" not "never
store."

**Why duplicate the REST ``audit_mutation`` events?** Deliberate —
programmatic paths (agent tools, background workers, team workers)
bypass the route layer, so without a provider-boundary emitter they
only produce ``provider.call`` and operators lose ``memory.record.write``
traceability.  The REST path's double event is the same shape and
correlated via ``request_id``.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from typing import Any

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.primitives._metadata_scrub import apply_metadata_denylist, get_denylist


def _extract_record_metadata(record: Any) -> Iterable[dict[str, Any]]:
    """Yield the single metadata dict on a ``MemoryRecord``."""
    meta = getattr(record, "metadata", None)
    if isinstance(meta, dict):
        yield meta


def _extract_search_result_metadata(result: Any) -> Iterable[dict[str, Any]]:
    """Yield the metadata dict nested inside ``SearchResult.record``."""
    record = getattr(result, "record", None)
    if record is not None:
        yield from _extract_record_metadata(record)


def wrap_retrieve(func: Any) -> Any:
    """Wrap ``retrieve`` to scrub metadata on the returned record.

    ``retrieve`` returns a single ``MemoryRecord | None``, so the
    helper sees a one-element iterable or nothing.  ``None`` results
    (missing key) are naturally skipped inside ``apply_metadata_denylist``.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = await func(*args, **kwargs)
        if result is not None:
            apply_metadata_denylist([result], get_denylist("memory"), extract=_extract_record_metadata)
        return result

    return wrapper


def wrap_search(func: Any) -> Any:
    """Wrap ``search`` to scrub metadata on each result's nested record.

    ``search`` returns ``list[SearchResult]`` where the metadata lives
    under ``result.record.metadata`` — the extractor handles the
    nested reach-in so call sites don't need to know the shape.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        results = await func(*args, **kwargs)
        apply_metadata_denylist(results, get_denylist("memory"), extract=_extract_search_result_metadata)
        return results

    return wrapper


def wrap_list_memories(func: Any) -> Any:
    """Wrap ``list_memories`` to scrub metadata on every record in the list."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        records = await func(*args, **kwargs)
        apply_metadata_denylist(records, get_denylist("memory"), extract=_extract_record_metadata)
        return records

    return wrapper


# ── Write-path audit wrappers ────────────────────────────────────────
#
# Each wrapper emits the matching ``memory.*`` action on success and
# failure so agent-tool invocations produce the same specific event
# the REST path already emits via ``audit_mutation``.  Non-secret
# identifiers (namespace, key, memory_id) are the ``resource_id``;
# content is intentionally omitted from metadata.
#
# **Signature note:** these wrappers use the exact ABC signatures,
# not ``*args, **kwargs``.  If a subclass extends the signature with
# an extra kwarg, Python's binding will fail at the wrapper boundary
# rather than silently dropping the arg.  That's the intentional
# trade-off — the LLM / knowledge wrappers do the same — subclasses
# are expected to honor the ABC's method signatures, not extend them.
# To extend, update the ABC (which updates the wrapper in lockstep).


def _emit_memory_event(
    action: str,
    outcome: AuditOutcome,
    *,
    resource_id: str | None,
    metadata: dict[str, Any],
) -> None:
    # See tools/_audit.py::_emit for the ``layer`` rationale — lets
    # operators split provider-boundary events from the route-layer
    # ``audit_mutation`` event that shares the same action + request_id.
    metadata.setdefault("layer", "primitive")
    emit_audit_event(
        action=action,
        outcome=outcome,
        resource_type=ResourceType.MEMORY,
        resource_id=resource_id,
        metadata=metadata,
    )


def wrap_store(func: Any) -> Any:
    """Wrap ``store`` to emit ``memory.record.write`` with namespace/key."""

    @functools.wraps(func)
    async def wrapper(
        self: Any,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        try:
            result = await func(self, namespace, key, content, metadata)
        except Exception as exc:
            _emit_memory_event(
                AuditAction.MEMORY_RECORD_WRITE,
                AuditOutcome.ERROR,
                resource_id=f"{namespace}/{key}",
                metadata={"namespace": namespace, "key": key, "error_type": type(exc).__name__},
            )
            raise
        _emit_memory_event(
            AuditAction.MEMORY_RECORD_WRITE,
            AuditOutcome.SUCCESS,
            resource_id=f"{namespace}/{key}",
            metadata={"namespace": namespace, "key": key},
        )
        return result

    return wrapper


def wrap_delete(func: Any) -> Any:
    """Wrap ``delete`` to emit ``memory.record.delete``."""

    @functools.wraps(func)
    async def wrapper(self: Any, namespace: str, key: str) -> Any:
        try:
            result = await func(self, namespace, key)
        except Exception as exc:
            _emit_memory_event(
                AuditAction.MEMORY_RECORD_DELETE,
                AuditOutcome.ERROR,
                resource_id=f"{namespace}/{key}",
                metadata={"namespace": namespace, "key": key, "error_type": type(exc).__name__},
            )
            raise
        _emit_memory_event(
            AuditAction.MEMORY_RECORD_DELETE,
            AuditOutcome.SUCCESS,
            resource_id=f"{namespace}/{key}",
            metadata={"namespace": namespace, "key": key, "deleted": bool(result)},
        )
        return result

    return wrapper


def wrap_create_event(func: Any) -> Any:
    """Wrap ``create_event`` to emit ``memory.event.append``."""

    @functools.wraps(func)
    async def wrapper(
        self: Any,
        actor_id: str,
        session_id: str,
        messages: list[tuple[str, str]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        try:
            result = await func(self, actor_id, session_id, messages, metadata=metadata)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit_memory_event(
                AuditAction.MEMORY_EVENT_APPEND,
                AuditOutcome.ERROR,
                resource_id=f"{actor_id}/{session_id}",
                metadata={
                    "actor_id": actor_id,
                    "session_id": session_id,
                    "message_count": len(messages) if messages is not None else 0,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        event_id = result.get("event_id") if isinstance(result, dict) else None
        _emit_memory_event(
            AuditAction.MEMORY_EVENT_APPEND,
            AuditOutcome.SUCCESS,
            resource_id=f"{actor_id}/{session_id}",
            metadata={
                "actor_id": actor_id,
                "session_id": session_id,
                "message_count": len(messages) if messages is not None else 0,
                "event_id": event_id,
            },
        )
        return result

    return wrapper


def wrap_delete_event(func: Any) -> Any:
    """Wrap ``delete_event`` to emit ``memory.event.delete``."""

    @functools.wraps(func)
    async def wrapper(self: Any, actor_id: str, session_id: str, event_id: str) -> Any:
        try:
            result = await func(self, actor_id, session_id, event_id)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit_memory_event(
                AuditAction.MEMORY_EVENT_DELETE,
                AuditOutcome.ERROR,
                resource_id=event_id,
                metadata={
                    "actor_id": actor_id,
                    "session_id": session_id,
                    "event_id": event_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        _emit_memory_event(
            AuditAction.MEMORY_EVENT_DELETE,
            AuditOutcome.SUCCESS,
            resource_id=event_id,
            metadata={"actor_id": actor_id, "session_id": session_id, "event_id": event_id},
        )
        return result

    return wrapper


def wrap_fork_conversation(func: Any) -> Any:
    """Wrap ``fork_conversation`` to emit ``memory.branch.create``."""

    @functools.wraps(func)
    async def wrapper(
        self: Any,
        actor_id: str,
        session_id: str,
        root_event_id: str,
        branch_name: str,
        messages: list[tuple[str, str]],
    ) -> Any:
        try:
            result = await func(self, actor_id, session_id, root_event_id, branch_name, messages)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit_memory_event(
                AuditAction.MEMORY_BRANCH_CREATE,
                AuditOutcome.ERROR,
                resource_id=f"{actor_id}/{session_id}/{branch_name}",
                metadata={
                    "actor_id": actor_id,
                    "session_id": session_id,
                    "root_event_id": root_event_id,
                    "branch_name": branch_name,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        _emit_memory_event(
            AuditAction.MEMORY_BRANCH_CREATE,
            AuditOutcome.SUCCESS,
            resource_id=f"{actor_id}/{session_id}/{branch_name}",
            metadata={
                "actor_id": actor_id,
                "session_id": session_id,
                "root_event_id": root_event_id,
                "branch_name": branch_name,
            },
        )
        return result

    return wrapper


def wrap_create_memory_resource(func: Any) -> Any:
    """Wrap ``create_memory_resource`` to emit ``memory.resource.create``."""

    @functools.wraps(func)
    async def wrapper(
        self: Any,
        name: str,
        *,
        strategies: list[dict[str, Any]] | None = None,
        description: str | None = None,
    ) -> Any:
        try:
            result = await func(self, name, strategies=strategies, description=description)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit_memory_event(
                AuditAction.MEMORY_RESOURCE_CREATE,
                AuditOutcome.ERROR,
                resource_id=name,
                metadata={"name": name, "error_type": type(exc).__name__},
            )
            raise
        memory_id = result.get("memory_id") if isinstance(result, dict) else None
        _emit_memory_event(
            AuditAction.MEMORY_RESOURCE_CREATE,
            AuditOutcome.SUCCESS,
            resource_id=memory_id or name,
            metadata={"name": name, "memory_id": memory_id},
        )
        return result

    return wrapper


def wrap_delete_memory_resource(func: Any) -> Any:
    """Wrap ``delete_memory_resource`` to emit ``memory.resource.delete``."""

    @functools.wraps(func)
    async def wrapper(self: Any, memory_id: str) -> Any:
        try:
            result = await func(self, memory_id)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit_memory_event(
                AuditAction.MEMORY_RESOURCE_DELETE,
                AuditOutcome.ERROR,
                resource_id=memory_id,
                metadata={"memory_id": memory_id, "error_type": type(exc).__name__},
            )
            raise
        _emit_memory_event(
            AuditAction.MEMORY_RESOURCE_DELETE,
            AuditOutcome.SUCCESS,
            resource_id=memory_id,
            metadata={"memory_id": memory_id},
        )
        return result

    return wrapper


def wrap_add_strategy(func: Any) -> Any:
    """Wrap ``add_strategy`` to emit ``memory.strategy.create``."""

    @functools.wraps(func)
    async def wrapper(self: Any, memory_id: str, strategy: dict[str, Any]) -> Any:
        try:
            result = await func(self, memory_id, strategy)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit_memory_event(
                AuditAction.MEMORY_STRATEGY_CREATE,
                AuditOutcome.ERROR,
                resource_id=memory_id,
                metadata={"memory_id": memory_id, "error_type": type(exc).__name__},
            )
            raise
        strategy_id = result.get("strategy_id") if isinstance(result, dict) else None
        _emit_memory_event(
            AuditAction.MEMORY_STRATEGY_CREATE,
            AuditOutcome.SUCCESS,
            resource_id=strategy_id or memory_id,
            metadata={"memory_id": memory_id, "strategy_id": strategy_id},
        )
        return result

    return wrapper


def wrap_delete_strategy(func: Any) -> Any:
    """Wrap ``delete_strategy`` to emit ``memory.strategy.delete``."""

    @functools.wraps(func)
    async def wrapper(self: Any, memory_id: str, strategy_id: str) -> Any:
        try:
            result = await func(self, memory_id, strategy_id)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit_memory_event(
                AuditAction.MEMORY_STRATEGY_DELETE,
                AuditOutcome.ERROR,
                resource_id=strategy_id,
                metadata={"memory_id": memory_id, "strategy_id": strategy_id, "error_type": type(exc).__name__},
            )
            raise
        _emit_memory_event(
            AuditAction.MEMORY_STRATEGY_DELETE,
            AuditOutcome.SUCCESS,
            resource_id=strategy_id,
            metadata={"memory_id": memory_id, "strategy_id": strategy_id},
        )
        return result

    return wrapper
