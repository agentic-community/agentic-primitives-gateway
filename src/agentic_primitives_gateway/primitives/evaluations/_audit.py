"""Evaluations audit wrappers used by ``EvaluationsProvider.__init_subclass__``.

Every subclass gets evaluator CRUD, score CRUD, and online-config CRUD
wrapped automatically to emit the matching ``evaluator.*`` action on
success and failure — so programmatic callers produce the same
specific event the REST path emits.  Uses ``AuditAction``'s existing
evaluator taxonomy (``EVALUATOR_CREATE/UPDATE/DELETE``,
``SCORE_CREATE/DELETE``, ``ONLINE_CONFIG_CREATE/DELETE``).
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
        resource_type=ResourceType.EVALUATOR,
        resource_id=resource_id,
        metadata=metadata,
    )


def wrap_create_evaluator(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        name: str,
        evaluator_type: str,
        config: dict[str, Any] | None = None,
        description: str = "",
    ) -> Any:
        try:
            result = await func(self, name, evaluator_type, config, description)
        except Exception as exc:
            _emit(
                AuditAction.EVALUATOR_CREATE,
                AuditOutcome.ERROR,
                resource_id=name,
                metadata={"name": name, "evaluator_type": evaluator_type, "error_type": type(exc).__name__},
            )
            raise
        evaluator_id = result.get("evaluator_id") if isinstance(result, dict) else None
        _emit(
            AuditAction.EVALUATOR_CREATE,
            AuditOutcome.SUCCESS,
            resource_id=evaluator_id or name,
            metadata={"name": name, "evaluator_type": evaluator_type, "evaluator_id": evaluator_id},
        )
        return result

    return wrapper


def wrap_update_evaluator(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        evaluator_id: str,
        config: dict[str, Any] | None = None,
        description: str | None = None,
    ) -> Any:
        try:
            result = await func(self, evaluator_id, config, description)
        except Exception as exc:
            _emit(
                AuditAction.EVALUATOR_UPDATE,
                AuditOutcome.ERROR,
                resource_id=evaluator_id,
                metadata={"evaluator_id": evaluator_id, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.EVALUATOR_UPDATE,
            AuditOutcome.SUCCESS,
            resource_id=evaluator_id,
            metadata={"evaluator_id": evaluator_id},
        )
        return result

    return wrapper


def wrap_delete_evaluator(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, evaluator_id: str) -> Any:
        try:
            result = await func(self, evaluator_id)
        except Exception as exc:
            _emit(
                AuditAction.EVALUATOR_DELETE,
                AuditOutcome.ERROR,
                resource_id=evaluator_id,
                metadata={"evaluator_id": evaluator_id, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.EVALUATOR_DELETE,
            AuditOutcome.SUCCESS,
            resource_id=evaluator_id,
            metadata={"evaluator_id": evaluator_id},
        )
        return result

    return wrapper


def wrap_create_score(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        *,
        name: str,
        value: float | str,
        trace_id: str | None = None,
        observation_id: str | None = None,
        comment: str | None = None,
        data_type: str | None = None,
        config_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        try:
            result = await func(
                self,
                name=name,
                value=value,
                trace_id=trace_id,
                observation_id=observation_id,
                comment=comment,
                data_type=data_type,
                config_id=config_id,
                metadata=metadata,
            )
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.SCORE_CREATE,
                AuditOutcome.ERROR,
                resource_id=name,
                metadata={"name": name, "trace_id": trace_id, "error_type": type(exc).__name__},
            )
            raise
        score_id = result.get("score_id") if isinstance(result, dict) else None
        _emit(
            AuditAction.SCORE_CREATE,
            AuditOutcome.SUCCESS,
            resource_id=score_id or name,
            metadata={"name": name, "trace_id": trace_id, "score_id": score_id},
        )
        return result

    return wrapper


def wrap_delete_score(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, score_id: str) -> Any:
        try:
            result = await func(self, score_id)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.SCORE_DELETE,
                AuditOutcome.ERROR,
                resource_id=score_id,
                metadata={"score_id": score_id, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.SCORE_DELETE,
            AuditOutcome.SUCCESS,
            resource_id=score_id,
            metadata={"score_id": score_id},
        )
        return result

    return wrapper


def wrap_create_online_evaluation_config(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        name: str,
        evaluator_ids: list[str],
        config: dict[str, Any] | None = None,
    ) -> Any:
        try:
            result = await func(self, name, evaluator_ids, config)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.ONLINE_CONFIG_CREATE,
                AuditOutcome.ERROR,
                resource_id=name,
                metadata={
                    "name": name,
                    "evaluator_count": len(evaluator_ids) if evaluator_ids else 0,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        config_id = result.get("config_id") if isinstance(result, dict) else None
        _emit(
            AuditAction.ONLINE_CONFIG_CREATE,
            AuditOutcome.SUCCESS,
            resource_id=config_id or name,
            metadata={
                "name": name,
                "config_id": config_id,
                "evaluator_count": len(evaluator_ids) if evaluator_ids else 0,
            },
        )
        return result

    return wrapper


def wrap_delete_online_evaluation_config(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, config_id: str) -> Any:
        try:
            result = await func(self, config_id)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.ONLINE_CONFIG_DELETE,
                AuditOutcome.ERROR,
                resource_id=config_id,
                metadata={"config_id": config_id, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.ONLINE_CONFIG_DELETE,
            AuditOutcome.SUCCESS,
            resource_id=config_id,
            metadata={"config_id": config_id},
        )
        return result

    return wrapper
