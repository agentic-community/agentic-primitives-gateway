"""Identity-specific audit wrappers used by ``IdentityProvider.__init_subclass__``.

Every subclass gets its credential/workload management + token read
methods wrapped automatically to emit the matching ``identity.*`` or
``credential.read`` action on success and failure — so agent tools
calling ``get_token`` / ``get_api_key`` produce the same specific event
the REST path already emits.

Token values + API keys are intentionally never written to metadata —
only the credential provider name, workload name, and outcome.
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


# ── Data plane — token / api key reads ────────────────────────────


def wrap_get_token(func: Any) -> Any:
    """Wrap ``get_token`` to emit ``credential.read``.

    The workload token (an opaque JWT) is never written to metadata;
    only the credential provider name and declared auth flow land in
    the event.  The returned access_token / authorization_url is also
    not logged.
    """

    @functools.wraps(func)
    async def wrapper(
        self: Any,
        credential_provider: str,
        workload_token: str,
        *,
        auth_flow: str = "M2M",
        scopes: list[str] | None = None,
        callback_url: str | None = None,
        force_auth: bool = False,
        session_uri: str | None = None,
        custom_state: str | None = None,
        custom_parameters: dict[str, str] | None = None,
    ) -> Any:
        metadata_base: dict[str, Any] = {
            "credential_provider": credential_provider,
            "auth_flow": auth_flow,
            "kind": "token",
        }
        try:
            result = await func(
                self,
                credential_provider,
                workload_token,
                auth_flow=auth_flow,
                scopes=scopes,
                callback_url=callback_url,
                force_auth=force_auth,
                session_uri=session_uri,
                custom_state=custom_state,
                custom_parameters=custom_parameters,
            )
        except Exception as exc:
            _emit(
                AuditAction.CREDENTIAL_READ,
                AuditOutcome.ERROR,
                resource_type=ResourceType.CREDENTIAL,
                resource_id=credential_provider,
                metadata={**metadata_base, "error_type": type(exc).__name__},
            )
            raise
        # Distinguish "token returned" vs "3LO auth_url returned" without
        # logging either value.
        result_kind = "access_token" if isinstance(result, dict) and result.get("access_token") else "authorization_url"
        _emit(
            AuditAction.CREDENTIAL_READ,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.CREDENTIAL,
            resource_id=credential_provider,
            metadata={**metadata_base, "result_kind": result_kind},
        )
        return result

    return wrapper


def wrap_get_api_key(func: Any) -> Any:
    """Wrap ``get_api_key`` to emit ``credential.read``."""

    @functools.wraps(func)
    async def wrapper(self: Any, credential_provider: str, workload_token: str) -> Any:
        metadata_base = {"credential_provider": credential_provider, "kind": "api_key"}
        try:
            result = await func(self, credential_provider, workload_token)
        except Exception as exc:
            _emit(
                AuditAction.CREDENTIAL_READ,
                AuditOutcome.ERROR,
                resource_type=ResourceType.CREDENTIAL,
                resource_id=credential_provider,
                metadata={**metadata_base, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.CREDENTIAL_READ,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.CREDENTIAL,
            resource_id=credential_provider,
            metadata=metadata_base,
        )
        return result

    return wrapper


# ── Control plane — credential provider CRUD ─────────────────────


def wrap_create_credential_provider(func: Any) -> Any:
    # ``config`` is intentionally NOT copied into metadata — it
    # typically contains ``client_secret`` / ``api_key`` / etc.  Only
    # ``name`` and ``provider_type`` land in the event.
    @functools.wraps(func)
    async def wrapper(self: Any, name: str, provider_type: str, config: dict[str, Any]) -> Any:
        try:
            result = await func(self, name, provider_type, config)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.IDENTITY_CREDENTIAL_PROVIDER_CREATE,
                AuditOutcome.ERROR,
                resource_type=ResourceType.IDENTITY,
                resource_id=name,
                metadata={"name": name, "provider_type": provider_type, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.IDENTITY_CREDENTIAL_PROVIDER_CREATE,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.IDENTITY,
            resource_id=name,
            metadata={"name": name, "provider_type": provider_type},
        )
        return result

    return wrapper


def wrap_update_credential_provider(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, name: str, config: dict[str, Any]) -> Any:
        try:
            result = await func(self, name, config)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.IDENTITY_CREDENTIAL_PROVIDER_UPDATE,
                AuditOutcome.ERROR,
                resource_type=ResourceType.IDENTITY,
                resource_id=name,
                metadata={"name": name, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.IDENTITY_CREDENTIAL_PROVIDER_UPDATE,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.IDENTITY,
            resource_id=name,
            metadata={"name": name},
        )
        return result

    return wrapper


def wrap_delete_credential_provider(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, name: str) -> Any:
        try:
            result = await func(self, name)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.IDENTITY_CREDENTIAL_PROVIDER_DELETE,
                AuditOutcome.ERROR,
                resource_type=ResourceType.IDENTITY,
                resource_id=name,
                metadata={"name": name, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.IDENTITY_CREDENTIAL_PROVIDER_DELETE,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.IDENTITY,
            resource_id=name,
            metadata={"name": name},
        )
        return result

    return wrapper


# ── Control plane — workload identity CRUD ───────────────────────


def wrap_create_workload_identity(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> Any:
        try:
            result = await func(self, name, allowed_return_urls=allowed_return_urls)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.IDENTITY_WORKLOAD_CREATE,
                AuditOutcome.ERROR,
                resource_type=ResourceType.IDENTITY,
                resource_id=name,
                metadata={"name": name, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.IDENTITY_WORKLOAD_CREATE,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.IDENTITY,
            resource_id=name,
            metadata={"name": name},
        )
        return result

    return wrapper


def wrap_update_workload_identity(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(
        self: Any,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> Any:
        try:
            result = await func(self, name, allowed_return_urls=allowed_return_urls)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.IDENTITY_WORKLOAD_UPDATE,
                AuditOutcome.ERROR,
                resource_type=ResourceType.IDENTITY,
                resource_id=name,
                metadata={"name": name, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.IDENTITY_WORKLOAD_UPDATE,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.IDENTITY,
            resource_id=name,
            metadata={"name": name},
        )
        return result

    return wrapper


def wrap_delete_workload_identity(func: Any) -> Any:
    @functools.wraps(func)
    async def wrapper(self: Any, name: str) -> Any:
        try:
            result = await func(self, name)
        except NotImplementedError:
            raise
        except Exception as exc:
            _emit(
                AuditAction.IDENTITY_WORKLOAD_DELETE,
                AuditOutcome.ERROR,
                resource_type=ResourceType.IDENTITY,
                resource_id=name,
                metadata={"name": name, "error_type": type(exc).__name__},
            )
            raise
        _emit(
            AuditAction.IDENTITY_WORKLOAD_DELETE,
            AuditOutcome.SUCCESS,
            resource_type=ResourceType.IDENTITY,
            resource_id=name,
            metadata={"name": name},
        )
        return result

    return wrapper
