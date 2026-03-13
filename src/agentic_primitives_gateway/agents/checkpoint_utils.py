"""Shared checkpoint serialization for auth context and provider overrides.

Both AgentRunner and TeamRunner need to save/restore the authenticated
principal and credentials when checkpointing runs for crash recovery.
They also share identical provider-override save/restore logic when
delegating to agents with different provider configurations.

This module extracts the shared logic.
"""

from __future__ import annotations

from typing import Any

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import (
    AWSCredentials,
    _service_credentials,
    get_authenticated_principal,
    get_aws_credentials,
    get_provider_override,
    set_authenticated_principal,
    set_aws_credentials,
    set_provider_overrides,
    set_service_credentials,
)
from agentic_primitives_gateway.models.agents import AgentSpec
from agentic_primitives_gateway.models.enums import Primitive


def serialize_auth_context() -> dict[str, Any]:
    """Capture current auth context (principal + credentials) for checkpoint storage."""
    data: dict[str, Any] = {}
    principal = get_authenticated_principal()
    if principal is not None:
        data["principal"] = {
            "id": principal.id,
            "type": principal.type,
            "groups": list(principal.groups),
            "scopes": list(principal.scopes),
        }
    aws_creds = get_aws_credentials()
    if aws_creds:
        data["aws_credentials"] = {
            "access_key_id": aws_creds.access_key_id,
            "secret_access_key": aws_creds.secret_access_key,
            "session_token": aws_creds.session_token,
            "region": aws_creds.region,
        }
    svc_creds = _service_credentials.get()
    if svc_creds:
        data["service_credentials"] = svc_creds
    return data


def restore_auth_context(data: dict[str, Any]) -> AuthenticatedPrincipal:
    """Restore auth context from checkpoint data. Returns the reconstructed principal.

    Raises ValueError if principal data is missing.
    """
    p = data.get("principal")
    if not p or "id" not in p:
        raise ValueError("Checkpoint is missing principal data — cannot resume")
    principal = AuthenticatedPrincipal(
        id=p["id"],
        type=p.get("type", "user"),
        groups=frozenset(p.get("groups", [])),
        scopes=frozenset(p.get("scopes", [])),
    )
    set_authenticated_principal(principal)

    aws_data = data.get("aws_credentials")
    if aws_data:
        set_aws_credentials(
            AWSCredentials(
                access_key_id=aws_data["access_key_id"],
                secret_access_key=aws_data["secret_access_key"],
                session_token=aws_data.get("session_token"),
                region=aws_data.get("region"),
            )
        )
    svc_data = data.get("service_credentials")
    if svc_data:
        set_service_credentials(svc_data)

    return principal


def apply_provider_overrides(spec: AgentSpec) -> dict[str, str]:
    """Apply this agent's provider overrides, returning the previous ones.

    Provider overrides are stored in a request-scoped contextvar. When a
    coordinator delegates to a sub-agent, the sub-agent may have different
    overrides (e.g. memory: mem0 vs in_memory). We save the current state,
    merge the agent's overrides on top (agent wins on conflict), and return
    the saved state so restore_provider_overrides can put it back after the
    sub-agent finishes. This ensures the coordinator resumes with its own
    providers.
    """
    prev: dict[str, str] = {}
    for prim in Primitive:
        val = get_provider_override(prim)
        if val:
            prev[prim] = val
    if spec.provider_overrides:
        set_provider_overrides({**prev, **spec.provider_overrides})
    return prev


def restore_provider_overrides(prev: dict[str, str]) -> None:
    """Restore previous provider overrides."""
    set_provider_overrides(prev)
