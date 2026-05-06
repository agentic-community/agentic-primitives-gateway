"""Policy-specific audit wrappers used by ``PolicyProvider.__init_subclass__``.

Every subclass gets policy-engine CRUD and policy CRUD wrapped
automatically to emit the matching ``policy.*`` action (per
``AuditAction``) on success and failure.  Auditing at the provider
boundary covers programmatic + background callers that bypass the REST
route's ``audit_mutation`` emit.

Policy bodies are intentionally excluded from metadata — treat the
raw Cedar source as sensitive (can reference principal attributes,
service-specific ARNs, etc.).  Only IDs and body-length land in the
event.
"""

from __future__ import annotations

import functools
from typing import Any

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType


def _emit(
    action: str,
    outcome: AuditOutcome,
    *,
    resource_type: ResourceType,
    resource_id: str | None,
    metadata: dict[str, Any],
) -> None:
    # See tools/_audit.py::_emit for the ``layer`` rationale.
    metadata.setdefault("layer", "primitive")
    emit_audit_event(
        action=action,
        outcome=outcome,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata=metadata,
    )


# ── Policy engines ───────────────────────────────────────────────


def wrap_create_policy_engine(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        name: str,
        description: str = "",
        config: dict[str, Any] | None = None,
    ) -> Any:
        try:
            result = await func(self, name, description, config)
        except Exception as exc:
            _emit(
                AuditAction.POLICY_CREATE,
                AuditOutcome.ERROR,
                resource_type=ResourceType.POLICY_ENGINE,
                resource_id=name,
                metadata={"name": name, "kind": "engine", "error_type": type(exc).__name__},
            )
            raise
        engine_id = result.get("engine_id") if isinstance(result, dict) else None
        _emit(
            AuditAction.POLICY_CREATE,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.POLICY_ENGINE,
            resource_id=engine_id or name,
            metadata={"name": name, "kind": "engine", "engine_id": engine_id},
        )
        return result

    return wrapper


def wrap_delete_policy_engine(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, engine_id: str) -> Any:
        try:
            result = await func(self, engine_id)
        except Exception as exc:
            _emit(
                AuditAction.POLICY_DELETE,
                AuditOutcome.ERROR,
                resource_type=ResourceType.POLICY_ENGINE,
                resource_id=engine_id,
                metadata={"engine_id": engine_id, "kind": "engine", "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.POLICY_DELETE,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.POLICY_ENGINE,
            resource_id=engine_id,
            metadata={"engine_id": engine_id, "kind": "engine"},
        )
        return result

    return wrapper


# ── Policies ─────────────────────────────────────────────────────


def wrap_create_policy(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        engine_id: str,
        policy_body: str,
        description: str = "",
    ) -> Any:
        body_length = len(policy_body) if policy_body is not None else 0
        try:
            result = await func(self, engine_id, policy_body, description)
        except Exception as exc:
            _emit(
                AuditAction.POLICY_CREATE,
                AuditOutcome.ERROR,
                resource_type=ResourceType.POLICY,
                resource_id=engine_id,
                metadata={
                    "engine_id": engine_id,
                    "kind": "policy",
                    "body_length": body_length,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        policy_id = result.get("policy_id") if isinstance(result, dict) else None
        _emit(
            AuditAction.POLICY_CREATE,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.POLICY,
            resource_id=policy_id or engine_id,
            metadata={
                "engine_id": engine_id,
                "policy_id": policy_id,
                "kind": "policy",
                "body_length": body_length,
            },
        )
        return result

    return wrapper


def wrap_update_policy(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        engine_id: str,
        policy_id: str,
        policy_body: str,
        description: str | None = None,
    ) -> Any:
        body_length = len(policy_body) if policy_body is not None else 0
        try:
            result = await func(self, engine_id, policy_id, policy_body, description)
        except Exception as exc:
            _emit(
                AuditAction.POLICY_UPDATE,
                AuditOutcome.ERROR,
                resource_type=ResourceType.POLICY,
                resource_id=policy_id,
                metadata={
                    "engine_id": engine_id,
                    "policy_id": policy_id,
                    "body_length": body_length,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        _emit(
            AuditAction.POLICY_UPDATE,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.POLICY,
            resource_id=policy_id,
            metadata={"engine_id": engine_id, "policy_id": policy_id, "body_length": body_length},
        )
        return result

    return wrapper


def wrap_delete_policy(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, engine_id: str, policy_id: str) -> Any:
        try:
            result = await func(self, engine_id, policy_id)
        except Exception as exc:
            _emit(
                AuditAction.POLICY_DELETE,
                AuditOutcome.ERROR,
                resource_type=ResourceType.POLICY,
                resource_id=policy_id,
                metadata={
                    "engine_id": engine_id,
                    "policy_id": policy_id,
                    "kind": "policy",
                    "error_type": type(exc).__name__,
                },
            )
            raise
        _emit(
            AuditAction.POLICY_DELETE,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.POLICY,
            resource_id=policy_id,
            metadata={"engine_id": engine_id, "policy_id": policy_id, "kind": "policy"},
        )
        return result

    return wrapper
