"""Credential management routes."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.context import get_access_token
from agentic_primitives_gateway.credentials.base import CredentialResolver
from agentic_primitives_gateway.credentials.models import (
    CredentialStatus,
    CredentialUpdateRequest,
    MaskedCredentials,
    mask_value,
)
from agentic_primitives_gateway.credentials.writer.base import CredentialWriter
from agentic_primitives_gateway.routes._helpers import require_principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])

_writer: CredentialWriter | None = None
_resolver: CredentialResolver | None = None


def set_credential_writer(writer: CredentialWriter) -> None:
    global _writer
    _writer = writer


def set_credential_resolver(resolver: CredentialResolver) -> None:
    global _resolver
    _resolver = resolver


def _invalidate_cache(user_id: str) -> None:
    """Invalidate the credential cache for a user after a write/delete."""
    if _resolver is None:
        return
    cache = getattr(_resolver, "_cache", None)
    if cache is not None:
        cache.invalidate(user_id)


def _require_writer() -> CredentialWriter:
    if _writer is None:
        raise HTTPException(status_code=501, detail="Credential management is not configured")
    return _writer


def _require_access_token() -> str:
    token = get_access_token()
    if not token:
        raise HTTPException(status_code=401, detail="Access token required for credential operations")
    return token


@router.get("")
async def read_credentials() -> MaskedCredentials:
    """Read current user's credentials (secrets masked)."""
    writer = _require_writer()
    principal = require_principal()
    access_token = _require_access_token()

    try:
        raw = await writer.read(principal, access_token)
    except httpx.HTTPStatusError as e:
        logger.warning("Credential read failed: %s %s", e.response.status_code, e.response.text[:200])
        raise HTTPException(
            status_code=502,
            detail=f"Identity provider returned {e.response.status_code}",
        ) from None
    except Exception:
        logger.exception("Credential read failed")
        raise HTTPException(status_code=502, detail="Failed to read credentials from identity provider") from None

    masked = {k: mask_value(str(v)) for k, v in raw.items() if v}
    return MaskedCredentials(attributes=masked)


@router.put("")
async def write_credentials(body: CredentialUpdateRequest) -> dict[str, str]:
    """Write/update credentials to the OIDC provider."""
    writer = _require_writer()
    principal = require_principal()
    access_token = _require_access_token()

    changed_keys = sorted((body.attributes or {}).keys())
    try:
        await writer.write(principal, access_token, body)
    except NotImplementedError:
        emit_audit_event(
            action=AuditAction.CREDENTIAL_WRITE,
            outcome=AuditOutcome.ERROR,
            resource_type=ResourceType.CREDENTIAL,
            resource_id=principal.id,
            reason="not_configured",
            metadata={"keys": changed_keys},
        )
        raise HTTPException(status_code=501, detail="Credential writing is not configured") from None
    except httpx.HTTPStatusError as e:
        emit_audit_event(
            action=AuditAction.CREDENTIAL_WRITE,
            outcome=AuditOutcome.FAILURE,
            resource_type=ResourceType.CREDENTIAL,
            resource_id=principal.id,
            reason=f"idp_{e.response.status_code}",
            metadata={"keys": changed_keys},
        )
        metrics.CREDENTIAL_OPS.labels(op="write", service="user_profile", outcome="failure").inc()
        logger.warning("Credential write failed: %s %s", e.response.status_code, e.response.text[:200])
        raise HTTPException(
            status_code=502,
            detail=f"Identity provider returned {e.response.status_code}",
        ) from None
    except Exception:
        emit_audit_event(
            action=AuditAction.CREDENTIAL_WRITE,
            outcome=AuditOutcome.ERROR,
            resource_type=ResourceType.CREDENTIAL,
            resource_id=principal.id,
            metadata={"keys": changed_keys},
        )
        metrics.CREDENTIAL_OPS.labels(op="write", service="user_profile", outcome="error").inc()
        logger.exception("Credential write failed")
        raise HTTPException(status_code=502, detail="Failed to write credentials to identity provider") from None

    _invalidate_cache(principal.id)
    emit_audit_event(
        action=AuditAction.CREDENTIAL_WRITE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.CREDENTIAL,
        resource_id=principal.id,
        metadata={"keys": changed_keys},
    )
    metrics.CREDENTIAL_OPS.labels(op="write", service="user_profile", outcome="success").inc()
    return {"status": "updated"}


@router.delete("/{key:path}")
async def delete_credential(key: str) -> dict[str, str]:
    """Delete a single credential by attribute name."""
    writer = _require_writer()
    principal = require_principal()
    access_token = _require_access_token()

    try:
        await writer.delete(principal, access_token, key)
    except NotImplementedError:
        emit_audit_event(
            action=AuditAction.CREDENTIAL_DELETE,
            outcome=AuditOutcome.ERROR,
            resource_type=ResourceType.CREDENTIAL,
            resource_id=principal.id,
            reason="not_configured",
            metadata={"keys": [key]},
        )
        raise HTTPException(status_code=501, detail="Credential deletion is not configured") from None
    except httpx.HTTPStatusError as e:
        emit_audit_event(
            action=AuditAction.CREDENTIAL_DELETE,
            outcome=AuditOutcome.FAILURE,
            resource_type=ResourceType.CREDENTIAL,
            resource_id=principal.id,
            reason=f"idp_{e.response.status_code}",
            metadata={"keys": [key]},
        )
        metrics.CREDENTIAL_OPS.labels(op="delete", service="user_profile", outcome="failure").inc()
        logger.warning("Credential delete failed: %s %s", e.response.status_code, e.response.text[:200])
        raise HTTPException(
            status_code=502,
            detail=f"Identity provider returned {e.response.status_code}",
        ) from None
    except Exception:
        emit_audit_event(
            action=AuditAction.CREDENTIAL_DELETE,
            outcome=AuditOutcome.ERROR,
            resource_type=ResourceType.CREDENTIAL,
            resource_id=principal.id,
            metadata={"keys": [key]},
        )
        metrics.CREDENTIAL_OPS.labels(op="delete", service="user_profile", outcome="error").inc()
        logger.exception("Credential delete failed")
        raise HTTPException(status_code=502, detail="Failed to delete credential") from None

    _invalidate_cache(principal.id)
    emit_audit_event(
        action=AuditAction.CREDENTIAL_DELETE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.CREDENTIAL,
        resource_id=principal.id,
        metadata={"keys": [key]},
    )
    metrics.CREDENTIAL_OPS.labels(op="delete", service="user_profile", outcome="success").inc()
    return {"status": "deleted"}


@router.get("/status")
async def credential_status() -> CredentialStatus:
    """Check credential resolution status for the current user."""
    from agentic_primitives_gateway.config import settings

    source = "none"
    aws_configured = False

    creds_cfg = getattr(settings, "credentials", None)
    if creds_cfg is not None and creds_cfg.resolver != "noop":
        source = creds_cfg.resolver
        if hasattr(creds_cfg, "oidc"):
            aws_configured = creds_cfg.oidc.aws.enabled

    server_credentials = settings.allow_server_credentials.value

    # Derive required credential types from active provider config
    required = _derive_required_credentials(settings)

    return CredentialStatus(
        source=source,
        aws_configured=aws_configured,
        server_credentials=server_credentials,
        required_credentials=required,
    )


# Provider class names that need AWS credentials
_AWS_PROVIDERS = {"AgentCore", "Bedrock"}
# Provider class name substring → service credential key
_SERVICE_PROVIDERS: dict[str, str] = {
    "Langfuse": "langfuse",
    "Mem0": "mem0",
    "Okta": "okta",
    "Keycloak": "keycloak",
    "MCPRegistry": "mcp_registry",
    "SeleniumGrid": "selenium",
}


def _derive_required_credentials(settings: object) -> list[str]:
    """Inspect active providers to determine what credentials are needed."""
    providers_cfg = getattr(settings, "providers", None)
    if not providers_cfg:
        return []

    required: set[str] = set()
    provider_dict = providers_cfg if isinstance(providers_cfg, dict) else {}

    for _primitive, prim_cfg in provider_dict.items():
        if not isinstance(prim_cfg, dict):
            continue
        # Check both single-backend and multi-backend formats
        backends = prim_cfg.get("backends", {})
        if not backends and "backend" in prim_cfg:
            backends = {"default": prim_cfg}
        for _name, backend_cfg in backends.items():
            if not isinstance(backend_cfg, dict):
                continue
            backend_path = backend_cfg.get("backend", "")
            class_name = backend_path.rsplit(".", 1)[-1] if backend_path else ""
            # Check AWS
            if any(p in class_name for p in _AWS_PROVIDERS):
                required.add("aws")
            # Check service credentials
            for pattern, svc in _SERVICE_PROVIDERS.items():
                if pattern in class_name:
                    required.add(svc)

    return sorted(required)
