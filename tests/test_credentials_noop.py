"""Tests for noop credential resolver and writer."""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.auth.models import ANONYMOUS_PRINCIPAL, AuthenticatedPrincipal
from agentic_primitives_gateway.credentials.models import CredentialUpdateRequest
from agentic_primitives_gateway.credentials.noop import NoopCredentialResolver
from agentic_primitives_gateway.credentials.writer.noop import NoopCredentialWriter


class TestNoopCredentialResolver:
    @pytest.mark.asyncio
    async def test_resolve_returns_none(self):
        resolver = NoopCredentialResolver()
        principal = AuthenticatedPrincipal(id="user1", type="user")
        result = await resolver.resolve(principal, "some-token")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_anonymous_returns_none(self):
        resolver = NoopCredentialResolver()
        result = await resolver.resolve(ANONYMOUS_PRINCIPAL, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        resolver = NoopCredentialResolver()
        await resolver.close()  # Should not raise


class TestNoopCredentialWriter:
    @pytest.mark.asyncio
    async def test_write_raises(self):
        writer = NoopCredentialWriter()
        principal = AuthenticatedPrincipal(id="user1", type="user")
        with pytest.raises(NotImplementedError, match="not configured"):
            await writer.write(principal, "token", CredentialUpdateRequest(attributes={"k": "v"}))

    @pytest.mark.asyncio
    async def test_read_returns_empty(self):
        writer = NoopCredentialWriter()
        principal = AuthenticatedPrincipal(id="user1", type="user")
        result = await writer.read(principal, "token")
        assert result == {}

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        writer = NoopCredentialWriter()
        await writer.close()  # Should not raise
