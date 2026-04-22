"""Intent-level test: the client's key methods actually round-trip
through the real server and get the expected result.

Contract: for every server endpoint the client wraps, a client-side
call through the real ASGI app must produce the documented
response.  The existing ``client/tests/`` suite uses a hand-rolled
httpx mock that simulates server responses — if the real server
changes its response schema, the client tests silently keep passing
against the stale mock.

This file exercises ``store_memory → retrieve_memory → search_memory
→ delete_memory`` against a live ASGI app.  If the server's
response format drifts, these tests fail deterministically.
"""

from __future__ import annotations

import httpx
import pytest

from agentic_primitives_gateway.main import app
from agentic_primitives_gateway_client import AgenticPlatformClient


@pytest.fixture
async def client():
    """Client wired to the real ASGI app via httpx transport."""
    transport = httpx.ASGITransport(app=app)
    async with AgenticPlatformClient("http://test", transport=transport, max_retries=0) as c:
        yield c


class TestMemoryRoundTrip:
    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, client: AgenticPlatformClient):
        """Write a record, read it back.  Confirms the full chain:
        client serialization → POST /memory/{ns} → server route →
        InMemoryProvider.store → GET /memory/{ns}/{key} →
        server → InMemoryProvider.retrieve → client deserialization.
        """
        await client.store_memory("ns-roundtrip", "rt-key", "hello, world")
        record = await client.retrieve_memory("ns-roundtrip", "rt-key")
        assert record["content"] == "hello, world"
        assert record["namespace"] == "ns-roundtrip"
        assert record["key"] == "rt-key"

    @pytest.mark.asyncio
    async def test_store_with_metadata_survives_round_trip(self, client: AgenticPlatformClient):
        await client.store_memory(
            "ns-meta",
            "k1",
            "content",
            metadata={"tag": "docs", "priority": 3},
        )
        record = await client.retrieve_memory("ns-meta", "k1")
        assert record["metadata"] == {"tag": "docs", "priority": 3}

    @pytest.mark.asyncio
    async def test_search_returns_ranked_matches(self, client: AgenticPlatformClient):
        """Store three records; search returns them in score order.
        Confirms the SearchResult shape the server emits matches
        what the client deserializes and what the ranking test in
        test_search_ranking.py asserts at the provider level.
        """
        # Different lengths → different scores.
        await client.store_memory("search-ns", "short", "python")
        await client.store_memory("search-ns", "med", "python is a language")
        await client.store_memory("search-ns", "long", "python is a programming language")

        resp = await client.search_memory("search-ns", "python", top_k=10)
        results = resp["results"]
        assert len(results) == 3
        # Descending scores.
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_delete_removes_record(self, client: AgenticPlatformClient):
        await client.store_memory("del-ns", "to-delete", "bye")
        # Sanity: it's there.
        rec = await client.retrieve_memory("del-ns", "to-delete")
        assert rec["content"] == "bye"

        # delete_memory returns None on success (204).
        await client.delete_memory("del-ns", "to-delete")

        # Now retrieve returns 404 → AgenticPlatformError.
        from agentic_primitives_gateway_client import AgenticPlatformError

        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.retrieve_memory("del-ns", "to-delete")
        assert exc_info.value.status_code == 404


class TestHealthRoundTrip:
    @pytest.mark.asyncio
    async def test_healthz(self, client: AgenticPlatformClient):
        """healthz returns the expected shape."""
        resp = await client.healthz()
        assert resp["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readyz(self, client: AgenticPlatformClient):
        """readyz returns a status + per-primitive checks dict."""
        resp = await client.readyz()
        assert "status" in resp
