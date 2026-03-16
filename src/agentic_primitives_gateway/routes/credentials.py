"""Credential management routes."""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, HTTPException

from agentic_primitives_gateway.context import get_access_token
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


def set_credential_writer(writer: CredentialWriter) -> None:
    global _writer
    _writer = writer


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

    try:
        await writer.write(principal, access_token, body)
    except NotImplementedError:
        raise HTTPException(status_code=501, detail="Credential writing is not configured") from None
    except httpx.HTTPStatusError as e:
        logger.warning("Credential write failed: %s %s", e.response.status_code, e.response.text[:200])
        raise HTTPException(
            status_code=502,
            detail=f"Identity provider returned {e.response.status_code}",
        ) from None
    except Exception:
        logger.exception("Credential write failed")
        raise HTTPException(status_code=502, detail="Failed to write credentials to identity provider") from None

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
        raise HTTPException(status_code=501, detail="Credential deletion is not configured") from None
    except httpx.HTTPStatusError as e:
        logger.warning("Credential delete failed: %s %s", e.response.status_code, e.response.text[:200])
        raise HTTPException(
            status_code=502,
            detail=f"Identity provider returned {e.response.status_code}",
        ) from None
    except Exception:
        logger.exception("Credential delete failed")
        raise HTTPException(status_code=502, detail="Failed to delete credential") from None

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

    return CredentialStatus(
        source=source,
        aws_configured=aws_configured,
    )
