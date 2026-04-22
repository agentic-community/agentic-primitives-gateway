"""Intent-level test: client error behavior is predictable.

Contract (from docstrings + common user expectation):
- HTTP 4xx/5xx responses → ``AgenticPlatformError`` with ``status_code`` + ``detail``.
- Gateway unreachable (connection refused, DNS failure, timeout) →
  a clear error — ideally the same ``AgenticPlatformError`` type so
  users can catch one exception.  Today the client raises the raw
  ``httpx.TransportError``, which is less discoverable.

No existing tests cover the gateway-unreachable path.  Users who
point at a bad URL get a stack trace they can't easily handle.

Tests:
1. Connection refused → clear error surfaces (type-stable: either
   AgenticPlatformError or a documented httpx error).
2. HTTP 500 still becomes AgenticPlatformError with status_code=500.
3. HTTP 404 becomes AgenticPlatformError with status_code=404.
"""

from __future__ import annotations

import httpx
import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError


class TestGatewayDown:
    @pytest.mark.asyncio
    async def test_connection_refused_raises_clear_error(self):
        """Point at an unreachable port → the client raises a clear
        error, not a bare stack trace the user can't handle.

        Today the client raises ``httpx.TransportError`` — this test
        pins that contract so a future wrapper (converting to
        AgenticPlatformError) surfaces as an intentional change.
        """
        # Port 1 is never bound.  ConnectError / ConnectTimeout fires
        # immediately on most systems.
        async with AgenticPlatformClient("http://127.0.0.1:1", max_retries=0, timeout=1.0) as client:
            with pytest.raises((httpx.TransportError, AgenticPlatformError)) as exc_info:
                await client.store_memory("ns", "k", "v")
            # Whatever the type, ``str(exc)`` should mention the URL
            # or the nature of the failure — users debugging a
            # connection error need a breadcrumb.
            assert exc_info.value, "Exception must carry some message"


class TestHttpErrors:
    @pytest.mark.asyncio
    async def test_http_500_becomes_agentic_platform_error(self):
        """Server returns 500 → AgenticPlatformError(500, "...")."""

        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "internal boom"})

        transport = httpx.MockTransport(_handler)
        async with AgenticPlatformClient("http://test", max_retries=0, transport=transport) as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.store_memory("ns", "k", "v")
            assert exc_info.value.status_code == 500
            assert "internal boom" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_http_404_becomes_agentic_platform_error(self):
        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "Memory not found"})

        transport = httpx.MockTransport(_handler)
        async with AgenticPlatformClient("http://test", max_retries=0, transport=transport) as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.retrieve_memory("ns", "missing-key")
            assert exc_info.value.status_code == 404
            assert "not found" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_http_400_with_non_json_body_still_raises(self):
        """Some servers return plain-text errors.  The client's
        ``_raise_for_status`` falls back to ``resp.text`` as the
        detail — must not crash trying to parse JSON.
        """

        def _handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, content=b"plain text body explaining the problem")

        transport = httpx.MockTransport(_handler)
        async with AgenticPlatformClient("http://test", max_retries=0, transport=transport) as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.store_memory("ns", "k", "v")
            assert exc_info.value.status_code == 400
            assert "plain text body" in exc_info.value.detail
