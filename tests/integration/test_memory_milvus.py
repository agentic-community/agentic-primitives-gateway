"""Integration tests for the Mem0 + Milvus memory primitive.

Full stack with real Milvus and AWS Bedrock calls:
AgenticPlatformClient → ASGI → middleware → route → registry →
Mem0MemoryProvider → real mem0.Memory → Milvus vector DB + Bedrock LLM/embedder.

Requires:
  - A running Milvus instance (MILVUS_HOST / MILVUS_PORT env vars)
  - AWS credentials for Bedrock (LLM + embeddings)
"""

from __future__ import annotations

import asyncio
import os
import socket
from uuid import uuid4

import httpx
import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError

pytestmark = pytest.mark.integration


# ── Skip logic ────────────────────────────────────────────────────────


def _milvus_reachable() -> bool:
    """Check if Milvus is reachable via TCP."""
    host = os.environ.get("MILVUS_HOST", "localhost")
    port = int(os.environ.get("MILVUS_PORT", "19530"))
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


if not _milvus_reachable():
    pytest.skip("Milvus not reachable — skipping Milvus integration tests", allow_module_level=True)


# ── Registry initialization ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with Mem0 + Milvus memory provider (noop for everything else).

    Uses real Milvus for vector storage and real AWS Bedrock for LLM/embeddings.
    ``allow_server_credentials=True`` lets the provider use ambient AWS creds.
    """
    host = os.environ.get("MILVUS_HOST", "localhost")
    port = os.environ.get("MILVUS_PORT", "19530")
    token = os.environ.get("MILVUS_TOKEN", "")

    test_settings = Settings(
        allow_server_credentials=True,
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider",
                "config": {
                    "vector_store": {
                        "provider": "milvus",
                        "config": {
                            "collection_name": "integ_test_memories",
                            "url": f"http://{host}:{port}",
                            "token": token,
                            "embedding_model_dims": 1024,
                        },
                    },
                    "llm": {
                        "provider": "aws_bedrock",
                        "config": {
                            "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
                        },
                    },
                    "embedder": {
                        "provider": "aws_bedrock",
                        "config": {
                            "model": "amazon.titan-embed-text-v2:0",
                        },
                    },
                },
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "llm": {
                "backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider",
                "config": {},
            },
            "tools": {
                "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
                "config": {},
            },
            "identity": {
                "backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider",
                "config": {},
            },
            "code_interpreter": {
                "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider",
                "config": {},
            },
            "browser": {
                "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
                "config": {},
            },
        },
    )
    orig_settings = _config_module.settings
    _config_module.settings = test_settings
    _config_module.settings.allow_server_credentials = True
    registry.initialize(test_settings)

    yield

    _config_module.settings = orig_settings


# ── Client fixture ───────────────────────────────────────────────────


@pytest.fixture
async def client():
    """AgenticPlatformClient wired to the ASGI app with real AWS credentials."""
    transport = httpx.ASGITransport(app=app)
    async with AgenticPlatformClient(
        base_url="http://testserver",
        aws_from_environment=True,
        max_retries=2,
        transport=transport,
    ) as c:
        yield c


# ── Helpers ───────────────────────────────────────────────────────────


def _unique(prefix: str = "integ") -> str:
    return f"{prefix}-{uuid4().hex[:8]}"


async def _retrieve_with_retry(
    client: AgenticPlatformClient,
    ns: str,
    key: str,
    *,
    retries: int = 10,
    delay: float = 2.0,
) -> dict:
    """Retrieve a memory record, retrying on 404 for Milvus eventual consistency."""
    for attempt in range(retries):
        try:
            return await client.retrieve_memory(ns, key)
        except AgenticPlatformError as e:
            if e.status_code == 404 and attempt < retries - 1:
                await asyncio.sleep(delay)
                continue
            raise
    raise AssertionError("unreachable")


async def _list_with_retry(
    client: AgenticPlatformClient,
    ns: str,
    *,
    expected_min: int,
    limit: int = 100,
    retries: int = 10,
    delay: float = 2.0,
) -> dict:
    """List memories, retrying until at least ``expected_min`` records appear."""
    for attempt in range(retries):
        result = await client.list_memories(ns, limit=limit)
        if len(result.get("records", [])) >= expected_min:
            return result
        if attempt < retries - 1:
            await asyncio.sleep(delay)
    return result


# ── Key-value memory ─────────────────────────────────────────────────


class TestStoreAndRetrieve:
    async def test_store_and_retrieve(self, client: AgenticPlatformClient) -> None:
        ns = _unique("ns")
        key = _unique("key")

        record = await client.store_memory(ns, key, "hello milvus integration")

        assert record["namespace"] == ns
        assert record["key"] == key
        assert record["content"] == "hello milvus integration"

        retrieved = await _retrieve_with_retry(client, ns, key)

        assert retrieved["content"] == "hello milvus integration"
        assert retrieved["key"] == key

    async def test_store_with_metadata(self, client: AgenticPlatformClient) -> None:
        ns = _unique("ns")
        key = _unique("key")

        record = await client.store_memory(ns, key, "tagged content", {"env": "test", "version": "1"})

        assert record["metadata"]["env"] == "test"
        assert record["metadata"]["version"] == "1"


# ── Search ───────────────────────────────────────────────────────────


class TestSearchMemory:
    async def test_search_memory(self, client: AgenticPlatformClient) -> None:
        ns = _unique("ns")

        await client.store_memory(ns, "planets-1", "Mars is the fourth planet from the Sun")
        await client.store_memory(ns, "planets-2", "Jupiter is the largest planet in the solar system")
        await client.store_memory(ns, "food-1", "Pizza is a popular Italian dish")

        # Allow time for Milvus to index the vectors
        await asyncio.sleep(3)

        result = await client.search_memory(ns, "planets in our solar system", top_k=5)

        assert "results" in result
        assert len(result["results"]) >= 1


# ── Delete ───────────────────────────────────────────────────────────


class TestDeleteMemory:
    async def test_delete_memory(self, client: AgenticPlatformClient) -> None:
        ns = _unique("ns")
        key = _unique("key")

        await client.store_memory(ns, key, "to be deleted")

        # Verify it exists (with retry for indexing lag)
        await _retrieve_with_retry(client, ns, key)

        # Delete it
        await client.delete_memory(ns, key)

        # Verify it's gone — allow a few retries for deletion to propagate
        gone = False
        for _ in range(10):
            try:
                await client.retrieve_memory(ns, key)
                await asyncio.sleep(2)
            except AgenticPlatformError as e:
                if e.status_code == 404:
                    gone = True
                    break
                raise
        assert gone, "Memory record was not deleted"


# ── List memories ────────────────────────────────────────────────────


class TestListMemories:
    async def test_list_memories(self, client: AgenticPlatformClient) -> None:
        ns = _unique("ns")

        await client.store_memory(ns, "item-1", "first item")
        await client.store_memory(ns, "item-2", "second item")

        result = await _list_with_retry(client, ns, expected_min=2, limit=10)

        assert "records" in result
        assert len(result["records"]) >= 2
