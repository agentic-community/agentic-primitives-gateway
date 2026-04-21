"""System tests for the Mem0 memory primitive.

Full stack: AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
Mem0MemoryProvider -> (mocked) mem0.Memory.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError

# ── Registry override ────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with Mem0 memory provider (noop for everything else).

    ``allow_server_credentials="always"`` lets ``_with_aws_env`` yield without real
    AWS creds. We also patch the module-level ``settings`` singleton so that
    ``_server_credentials_allowed()`` returns True during request handling.
    """
    test_settings = Settings(
        allow_server_credentials="always",
        providers={
            "memory": {
                "backend": ("agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider"),
                "config": {},
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

    with patch("agentic_primitives_gateway.primitives.memory.mem0_provider.Memory") as mock_memory_cls:
        mock_client = MagicMock()
        mock_memory_cls.from_config.return_value = mock_client
        registry.initialize(test_settings)

        # Inject the mock client directly so lazy init finds it
        provider = registry.get_primitive("memory").get()
        provider._provider._client = mock_client

    yield

    _config_module.settings = orig_settings


# ── Helpers ──────────────────────────────────────────────────────────


def _get_mem0_client() -> MagicMock:
    """Return the mocked mem0 client from the live provider instance."""
    provider = registry.get_primitive("memory").get()
    return provider._provider._client


# ── Key-value memory ─────────────────────────────────────────────────


class TestStoreMemory:
    async def test_store_memory(self, client: AgenticPlatformClient) -> None:
        mem0 = _get_mem0_client()
        # No existing entry for this key
        mem0.get_all.return_value = {"results": []}

        record = await client.store_memory("ns1", "k1", "hello world", {"tag": "v1"})

        assert record["namespace"] == "ns1"
        assert record["key"] == "k1"
        assert record["content"] == "hello world"
        mem0.add.assert_called_once()


class TestRetrieveMemory:
    async def test_retrieve_existing(self, client: AgenticPlatformClient) -> None:
        mem0 = _get_mem0_client()
        mem0.get_all.return_value = {
            "results": [
                {
                    "id": "rec-1",
                    "memory": "hello world",
                    "metadata": {"_agentic_key": "k1"},
                }
            ]
        }

        record = await client.retrieve_memory("ns1", "k1")

        assert record["content"] == "hello world"
        assert record["key"] == "k1"

    async def test_retrieve_not_found(self, client: AgenticPlatformClient) -> None:
        mem0 = _get_mem0_client()
        mem0.get_all.return_value = {"results": []}

        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.retrieve_memory("ns1", "missing")
        assert exc_info.value.status_code == 404


class TestSearchMemory:
    async def test_search_memory(self, client: AgenticPlatformClient) -> None:
        mem0 = _get_mem0_client()
        mem0.search.return_value = {
            "results": [
                {
                    "id": "rec-1",
                    "memory": "matched content",
                    "score": 0.9,
                    "metadata": {"_agentic_key": "k1"},
                }
            ]
        }

        result = await client.search_memory("ns1", "query", top_k=5)

        assert "results" in result
        assert len(result["results"]) == 1
        assert result["results"][0]["score"] == 0.9


class TestDeleteMemory:
    async def test_delete_memory(self, client: AgenticPlatformClient) -> None:
        mem0 = _get_mem0_client()
        mem0.get_all.return_value = {
            "results": [
                {
                    "id": "rec-1",
                    "memory": "content",
                    "metadata": {"_agentic_key": "k1"},
                }
            ]
        }
        mem0.delete.return_value = None

        await client.delete_memory("ns1", "k1")

        mem0.delete.assert_called_once_with("rec-1")

    async def test_delete_not_found(self, client: AgenticPlatformClient) -> None:
        mem0 = _get_mem0_client()
        mem0.get_all.return_value = {"results": []}

        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.delete_memory("ns1", "missing")
        assert exc_info.value.status_code == 404


class TestListMemories:
    async def test_list_memories(self, client: AgenticPlatformClient) -> None:
        mem0 = _get_mem0_client()
        mem0.get_all.return_value = {
            "results": [
                {
                    "id": "rec-1",
                    "memory": "content-1",
                    "metadata": {"_agentic_key": "k1"},
                },
                {
                    "id": "rec-2",
                    "memory": "content-2",
                    "metadata": {"_agentic_key": "k2"},
                },
            ]
        }

        result = await client.list_memories("ns1", limit=10)

        assert "records" in result
        assert len(result["records"]) == 2
