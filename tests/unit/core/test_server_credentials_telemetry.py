"""Tests for degraded-mode surfacing on server-credential fallback.

Covers the two non-blocking observability fixes that ship alongside the
authz tenet:

1. **Startup warning** in ``main._warn_server_credentials_with_real_auth``
   when the deployment combines multi-user auth with shared gateway
   credentials.  Operators see the warning in their boot logs; it's not
   an error, just "verify this matches your intent."
2. **Runtime audit event** ``provider.server_credentials_used`` emitted
   from ``context._emit_server_credentials_used`` whenever the gateway
   attaches its own ambient credentials on behalf of a non-admin caller
   (under ``allow_server_credentials: fallback`` / ``always``).  Admin
   callers are excluded — ambient creds in an admin flow is expected.
"""

from __future__ import annotations

import logging

import pytest

from agentic_primitives_gateway.audit.models import AuditAction
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.context import (
    AWSCredentials,
    get_boto3_session,
    get_service_credentials_or_defaults,
    set_authenticated_principal,
    set_aws_credentials,
    set_service_credentials,
)
from agentic_primitives_gateway.main import _warn_server_credentials_with_real_auth
from agentic_primitives_gateway.models.enums import ServerCredentialMode

# ── Startup warning ──────────────────────────────────────────────────


class TestStartupWarning:
    def _with_auth_backend(self, backend: str):
        prev = settings.auth.backend
        settings.auth.backend = backend
        return prev

    def _with_cred_mode(self, mode):
        prev = settings.allow_server_credentials
        settings.allow_server_credentials = mode
        return prev

    def test_no_warning_under_noop_auth(self, caplog: pytest.LogCaptureFixture) -> None:
        """Noop auth is single-user dev mode — shared creds are expected."""
        prev_auth = self._with_auth_backend("noop")
        prev_mode = self._with_cred_mode(ServerCredentialMode.ALWAYS)
        try:
            with caplog.at_level(logging.WARNING, logger="agentic_primitives_gateway.main"):
                _warn_server_credentials_with_real_auth()
            assert "Server-credential warning" not in caplog.text
        finally:
            settings.auth.backend = prev_auth
            settings.allow_server_credentials = prev_mode

    def test_no_warning_under_never(self, caplog: pytest.LogCaptureFixture) -> None:
        """Never + real auth is the safer default — no ambient creds ever fire."""
        prev_auth = self._with_auth_backend("jwt")
        prev_mode = self._with_cred_mode(ServerCredentialMode.NEVER)
        try:
            with caplog.at_level(logging.WARNING, logger="agentic_primitives_gateway.main"):
                _warn_server_credentials_with_real_auth()
            assert "Server-credential warning" not in caplog.text
        finally:
            settings.auth.backend = prev_auth
            settings.allow_server_credentials = prev_mode

    def test_warns_on_jwt_plus_fallback(self, caplog: pytest.LogCaptureFixture) -> None:
        prev_auth = self._with_auth_backend("jwt")
        prev_mode = self._with_cred_mode(ServerCredentialMode.FALLBACK)
        try:
            with caplog.at_level(logging.WARNING, logger="agentic_primitives_gateway.main"):
                _warn_server_credentials_with_real_auth()
            assert "Server-credential warning" in caplog.text
            assert "jwt" in caplog.text
            assert "fallback" in caplog.text
        finally:
            settings.auth.backend = prev_auth
            settings.allow_server_credentials = prev_mode

    def test_warns_on_api_key_plus_always(self, caplog: pytest.LogCaptureFixture) -> None:
        prev_auth = self._with_auth_backend("api_key")
        prev_mode = self._with_cred_mode(ServerCredentialMode.ALWAYS)
        try:
            with caplog.at_level(logging.WARNING, logger="agentic_primitives_gateway.main"):
                _warn_server_credentials_with_real_auth()
            assert "Server-credential warning" in caplog.text
            assert "api_key" in caplog.text
            assert "always" in caplog.text
        finally:
            settings.auth.backend = prev_auth
            settings.allow_server_credentials = prev_mode


# ── Runtime audit event ──────────────────────────────────────────────


class _CollectorSink:
    """Minimal AuditSink that records emitted events into a list."""

    def __init__(self) -> None:
        self.name = "collector"
        self.events: list = []

    async def emit(self, event) -> None:
        self.events.append(event)


async def _collect(mode: ServerCredentialMode, setup):
    """Fire a boto3/service-cred resolution under ``mode`` and return emitted events.

    ``setup`` runs with the router wired in; it is expected to trigger
    the ambient-cred path via ``get_boto3_session`` or
    ``get_service_credentials_or_defaults``.
    """
    from agentic_primitives_gateway.audit.emit import set_audit_router
    from agentic_primitives_gateway.audit.router import AuditRouter

    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)
    prev_mode = settings.allow_server_credentials
    settings.allow_server_credentials = mode
    try:
        setup()
    finally:
        import asyncio

        await asyncio.sleep(0.02)
        await router.shutdown(timeout=1.0)
        set_audit_router(None)
        settings.allow_server_credentials = prev_mode
    return sink.events


def _non_admin() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(id="alice", type="user", scopes=frozenset())


def _admin() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(id="root", type="user", scopes=frozenset({"admin"}))


class TestServerCredentialsUsedEvent:
    """The event should fire for non-admin callers whenever the gateway
    falls back to its own credentials.  Admins don't trip it — their use
    of ambient creds is the documented operator path.
    """

    async def test_boto3_fallback_emits_for_non_admin(self) -> None:
        set_aws_credentials(None)
        set_authenticated_principal(_non_admin())
        try:
            events = await _collect(
                ServerCredentialMode.ALWAYS,
                lambda: get_boto3_session(default_region="us-east-1"),
            )
        finally:
            set_authenticated_principal(None)

        matching = [e for e in events if e.action == AuditAction.PROVIDER_SERVER_CREDENTIALS_USED]
        assert len(matching) == 1
        assert matching[0].metadata["service"] == "aws"
        assert matching[0].metadata["mode"] == "always"

    async def test_boto3_fallback_silent_for_admin(self) -> None:
        set_aws_credentials(None)
        set_authenticated_principal(_admin())
        try:
            events = await _collect(
                ServerCredentialMode.ALWAYS,
                lambda: get_boto3_session(default_region="us-east-1"),
            )
        finally:
            set_authenticated_principal(None)

        matching = [e for e in events if e.action == AuditAction.PROVIDER_SERVER_CREDENTIALS_USED]
        assert matching == []

    async def test_service_credentials_fallback_emits(self) -> None:
        """``fallback`` + any server-filled key → emit once.

        ``_emit_server_credentials_used`` is called from the helper
        when ``defaults`` contains truthy values the caller didn't
        supply — that's the "operator credentials actually fill gaps
        in the caller's request" state we want auditable.  If the
        defaults are all-None there are literally no operator values
        to fill with; no emit.
        """
        set_service_credentials({})
        set_authenticated_principal(_non_admin())
        try:
            events = await _collect(
                ServerCredentialMode.FALLBACK,
                lambda: get_service_credentials_or_defaults(
                    "langfuse", {"public_key": "server-pk", "secret_key": "server-sk"}
                ),
            )
        finally:
            set_authenticated_principal(None)

        matching = [e for e in events if e.action == AuditAction.PROVIDER_SERVER_CREDENTIALS_USED]
        assert len(matching) == 1
        assert matching[0].metadata["service"] == "langfuse"
        assert matching[0].metadata["mode"] == "fallback"

    async def test_caller_creds_present_no_event(self) -> None:
        """Ambient-cred path isn't taken when the caller presents their own."""
        set_aws_credentials(
            AWSCredentials(
                access_key_id="AKIAIOSFODNN7EXAMPLE",
                secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                region="us-west-2",
            )
        )
        set_authenticated_principal(_non_admin())
        try:
            events = await _collect(
                ServerCredentialMode.ALWAYS,
                lambda: get_boto3_session(default_region="us-east-1"),
            )
        finally:
            set_aws_credentials(None)
            set_authenticated_principal(None)

        matching = [e for e in events if e.action == AuditAction.PROVIDER_SERVER_CREDENTIALS_USED]
        assert matching == []
