from __future__ import annotations

import asyncio
import os
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import boto3
import pytest

from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.context import AWSCredentials, set_aws_credentials
from agentic_primitives_gateway.models.enums import ServerCredentialMode
from agentic_primitives_gateway.primitives.memory.mem0_provider import Mem0MemoryProvider


class TestBedrockSessionBinding:
    """mem0 builds Bedrock clients once from the env; each op must rebind them to the request session."""

    @pytest.fixture(autouse=True)
    def _ambient_env(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAAMBIENT")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret")
        monkeypatch.setenv("AWS_SESSION_TOKEN", "ambient-token")
        prev = settings.allow_server_credentials
        yield
        settings.allow_server_credentials = prev
        set_aws_credentials(None)

    def _provider(self) -> tuple[Mem0MemoryProvider, SimpleNamespace]:
        ambient = boto3.Session(region_name="us-west-2")
        memory = MagicMock()
        memory.llm = SimpleNamespace(client=ambient.client("bedrock-runtime"))
        memory.embedding_model = SimpleNamespace(client=ambient.client("bedrock-runtime"))
        memory.graph = None
        seen = SimpleNamespace(creds=[])

        def _get_all(**_):
            for c in (memory.llm.client, memory.embedding_model.client):
                frozen = c._request_signer._credentials.get_frozen_credentials()
                seen.creds.append((frozen.access_key, frozen.token))
            return {"results": []}

        memory.get_all.side_effect = _get_all
        provider = Mem0MemoryProvider(**_BEDROCK_CONFIG)
        provider._client = memory
        return provider, seen

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", [ServerCredentialMode.NEVER, ServerCredentialMode.FALLBACK])
    async def test_caller_credentials_used_without_ambient_session_token(self, mode):
        settings.allow_server_credentials = mode
        set_aws_credentials(AWSCredentials(access_key_id="AKIACALLER", secret_access_key="caller-secret"))
        provider, seen = self._provider()
        await provider.list_memories("ns")
        assert seen.creds == [("AKIACALLER", None), ("AKIACALLER", None)]
        assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIAAMBIENT"  # env never mutated

    @pytest.mark.asyncio
    async def test_always_ignores_caller_keys(self):
        settings.allow_server_credentials = ServerCredentialMode.ALWAYS
        set_aws_credentials(AWSCredentials(access_key_id="AKIACALLER", secret_access_key="caller-secret"))
        provider, seen = self._provider()
        await provider.list_memories("ns")
        assert {c[0] for c in seen.creds} == {"AKIAAMBIENT"}

    @pytest.mark.asyncio
    async def test_never_without_caller_credentials_refuses(self):
        settings.allow_server_credentials = ServerCredentialMode.NEVER
        provider, seen = self._provider()
        with pytest.raises(ValueError, match="No AWS credentials"):
            await provider.list_memories("ns")
        assert seen.creds == []

    @pytest.mark.asyncio
    async def test_construction_uses_caller_credentials_and_scrubs_them(self):
        """Mem0 must not retain the first caller's keys in any shared config."""
        settings.allow_server_credentials = ServerCredentialMode.NEVER
        set_aws_credentials(
            AWSCredentials(access_key_id="AKIACALLER", secret_access_key="caller-secret", session_token="caller-token")
        )
        built = MagicMock()
        built.llm = SimpleNamespace(
            config=SimpleNamespace(aws_access_key_id="x", aws_secret_access_key="y", aws_session_token="z")
        )
        built.embedding_model = SimpleNamespace(
            config=SimpleNamespace(aws_access_key_id="x", aws_secret_access_key="y")
        )
        built.config = SimpleNamespace(llm=SimpleNamespace(config={}), embedder=SimpleNamespace(config={}))
        built.graph = None
        built.get_all.return_value = {"results": []}
        provider = Mem0MemoryProvider(**_BEDROCK_CONFIG)
        seen = {}

        def construct(config):
            seen["llm"] = dict(config["llm"]["config"])
            seen["embedder"] = dict(config["embedder"]["config"])
            built.config.llm.config = dict(config["llm"]["config"])
            built.config.embedder.config = dict(config["embedder"]["config"])
            return built

        with patch("agentic_primitives_gateway.primitives.memory.mem0_provider.Memory") as memory_cls:
            memory_cls.from_config.side_effect = construct
            await provider.list_memories("ns")
        config = memory_cls.from_config.call_args.args[0]
        assert seen["llm"]["aws_access_key_id"] == "AKIACALLER"
        assert seen["llm"]["aws_session_token"] == "caller-token"
        assert seen["embedder"]["aws_access_key_id"] == "AKIACALLER"
        assert "aws_access_key_id" not in _BEDROCK_CONFIG["llm"]["config"]
        for name in ("llm", "embedder"):
            for field in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token"):
                assert field not in config[name]["config"]
                assert field not in getattr(built.config, name).config
        assert built.llm.config.aws_access_key_id is None
        assert built.llm.config.aws_secret_access_key is None
        assert built.llm.config.aws_session_token is None
        assert built.embedding_model.config.aws_access_key_id is None
        assert built.embedding_model.config.aws_secret_access_key is None
        assert provider._client is built

    @pytest.mark.asyncio
    async def test_failed_construction_scrubs_temporary_config(self):
        settings.allow_server_credentials = ServerCredentialMode.NEVER
        set_aws_credentials(AWSCredentials(access_key_id="AKIACALLER", secret_access_key="caller-secret"))
        provider = Mem0MemoryProvider(**_BEDROCK_CONFIG)
        seen = []

        def construct(config):
            seen.append(config)
            raise RuntimeError("failed initialization")

        with patch("agentic_primitives_gateway.primitives.memory.mem0_provider.Memory") as memory_cls:
            memory_cls.from_config.side_effect = construct
            with pytest.raises(RuntimeError, match="failed initialization"):
                await provider.list_memories("ns")
        assert len(seen) == 1
        for name in ("llm", "embedder"):
            for field in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token"):
                assert field not in seen[0][name]["config"]
        assert provider._client is None

    @pytest.mark.parametrize(
        "config",
        [
            {"vector_store": {"provider": "s3_vectors", "config": {}}},
            {"vector_store": {"provider": "neptune", "config": {}}},
            {"vector_store": {"provider": "future_aws_backend", "config": {}}},
            {"reranker": {"provider": "llm_reranker", "config": {"llm": {"provider": "aws_bedrock"}}}},
            {"graph_store": {"provider": "neo4j", "llm": {"provider": "aws_bedrock"}}},
        ],
    )
    def test_rejects_aws_components_it_cannot_bind(self, config):
        with pytest.raises(ValueError, match="refusing AWS-backed component"):
            Mem0MemoryProvider(**config)

    def test_neptune_rejected_before_mem0_construction(self):
        with patch("agentic_primitives_gateway.primitives.memory.mem0_provider.Memory") as memory_cls:
            with pytest.raises(ValueError, match="vector_store"):
                Mem0MemoryProvider(vector_store={"provider": "neptune", "config": {}})
            memory_cls.from_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_anonymous_bedrock_readiness_never_uses_ambient_or_prior_caller(self):
        settings.allow_server_credentials = ServerCredentialMode.NEVER
        provider, seen = self._provider()
        set_aws_credentials(AWSCredentials(access_key_id="AKIACALLER", secret_access_key="caller-secret"))
        await provider.list_memories("ns")
        assert seen.creds == [("AKIACALLER", None), ("AKIACALLER", None)]
        set_aws_credentials(None)
        with (
            patch.object(provider, "_get_client", side_effect=AssertionError("readiness touched caller client")),
            patch("agentic_primitives_gateway.primitives.memory.mem0_provider.get_boto3_session") as session,
            patch("agentic_primitives_gateway.primitives.memory.mem0_provider.Memory") as memory_cls,
        ):
            assert await provider.healthcheck() == "reachable"
            memory_cls.from_config.assert_not_called()
            session.assert_not_called()
        assert seen.creds == [("AKIACALLER", None), ("AKIACALLER", None)]

    @pytest.mark.asyncio
    async def test_anonymous_bedrock_readiness_does_not_initialize_client(self):
        settings.allow_server_credentials = ServerCredentialMode.NEVER
        provider = Mem0MemoryProvider(**_BEDROCK_CONFIG)
        with patch("agentic_primitives_gateway.primitives.memory.mem0_provider.Memory") as memory_cls:
            assert await provider.healthcheck() == "reachable"
            memory_cls.from_config.assert_not_called()
        assert provider._client is None

    @pytest.mark.asyncio
    async def test_bedrock_readiness_checks_milvus_without_initializing_aws(self):
        provider = Mem0MemoryProvider(
            **_BEDROCK_CONFIG,
            vector_store={
                "provider": "milvus",
                "config": {"url": "http://milvus:19530", "collection_name": "memories"},
            },
        )
        with (
            patch("agentic_primitives_gateway.primitives.memory.mem0_provider.Memory.from_config") as memory,
            patch("pymilvus.MilvusClient") as milvus,
        ):
            milvus.return_value.has_collection.return_value = True
            assert await provider.healthcheck() == "reachable"
            milvus.return_value.has_collection.assert_called_once_with("memories")
            milvus.return_value.close.assert_called_once()
            milvus.return_value.has_collection.return_value = False
            assert await provider.healthcheck() is False
            memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_bedrock_config_needs_no_aws_session_or_lock(self):
        settings.allow_server_credentials = ServerCredentialMode.NEVER  # no AWS creds at all
        provider = Mem0MemoryProvider(llm={"provider": "openai", "config": {}})
        provider._client = MagicMock()
        provider._client.get_all.return_value = {"results": []}
        provider._op_lock = MagicMock()
        await provider.list_memories("ns")
        provider._op_lock.__enter__.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_aws_readiness_checks_backend_without_aws_session(self):
        settings.allow_server_credentials = ServerCredentialMode.NEVER
        provider = Mem0MemoryProvider(vector_store={"provider": "milvus", "config": {}})
        provider._client = MagicMock()
        provider._client.get_all.return_value = {"results": []}
        with patch("agentic_primitives_gateway.primitives.memory.mem0_provider.get_boto3_session") as session:
            assert await provider.healthcheck() is True
            session.assert_not_called()
        provider._client.get_all.assert_called_once_with(filters={"user_id": "__healthcheck__"})


_BEDROCK_CONFIG = {
    "llm": {"provider": "aws_bedrock", "config": {"model": "m"}},
    "embedder": {"provider": "aws_bedrock", "config": {"model": "e"}},
}


@patch("agentic_primitives_gateway.context.get_aws_credentials")
class TestMem0MemoryProvider:
    """Tests for the Mem0 memory provider."""

    def _make_provider(self, **kwargs):
        prov = Mem0MemoryProvider(**kwargs)
        # Pre-initialize the client mock to avoid actual mem0 initialization
        prov._client = MagicMock()
        return prov

    @pytest.mark.asyncio
    async def test_store_new_record(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": []}

        result = await provider.store(namespace="ns", key="k1", content="hello")

        assert result.namespace == "ns"
        assert result.key == "k1"
        assert result.content == "hello"
        provider._client.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrent_same_key_stores_insert_only_once(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(vector_store={"provider": "milvus", "config": {}})
        entries: list[dict] = []
        first_read = threading.Event()
        second_read = threading.Event()
        calls = 0

        def get_all(*, filters):
            nonlocal calls
            assert filters == {"user_id": "ns"}
            calls += 1
            if calls == 1:
                first_read.set()
                # Without a lock, both requests observe the empty collection.
                second_read.wait(timeout=0.3)
            else:
                second_read.set()
            return {"results": list(entries)}

        def add(content, *, user_id, metadata, infer):
            assert user_id == "ns" and infer is False
            entries.append({"id": "mem-1", "metadata": metadata, "memory": content})

        def update(identifier, *, data):
            assert identifier == "mem-1"
            entries[0]["memory"] = data

        provider._client.get_all.side_effect = get_all
        provider._client.add.side_effect = add
        provider._client.update.side_effect = update
        first = asyncio.create_task(provider.store("ns", "key", "first"))
        assert await asyncio.to_thread(first_read.wait, 2)
        second = asyncio.create_task(provider.store("ns", "key", "second"))
        await asyncio.gather(first, second)

        assert len(entries) == 1
        assert provider._client.add.call_count == 1
        provider._client.update.assert_called_once_with("mem-1", data="second")

    @pytest.mark.asyncio
    async def test_non_bedrock_reads_not_blocked_by_store(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider()
        writing = threading.Event()
        release = threading.Event()

        def get_all(*, filters):
            if filters == {"user_id": "writing"}:
                writing.set()
                release.wait(timeout=2)
            return {"results": []}

        provider._client.get_all.side_effect = get_all
        write = asyncio.create_task(provider.store("writing", "key", "value"))
        try:
            assert await asyncio.to_thread(writing.wait, 2)
            assert await asyncio.wait_for(provider.retrieve("reading", "key"), timeout=1) is None
        finally:
            release.set()
            await write

    @pytest.mark.asyncio
    async def test_store_update_existing(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": [{"id": "mem-1", "metadata": {"_agentic_key": "k1"}}]}

        await provider.store(namespace="ns", key="k1", content="updated")

        provider._client.update.assert_called_once_with("mem-1", data="updated")

    @pytest.mark.asyncio
    async def test_retrieve_found(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {
            "results": [
                {
                    "id": "mem-1",
                    "memory": "found data",
                    "metadata": {"_agentic_key": "k1"},
                    "created_at": None,
                    "updated_at": None,
                },
            ]
        }

        result = await provider.retrieve(namespace="ns", key="k1")

        assert result is not None
        assert result.content == "found data"

    @pytest.mark.asyncio
    async def test_retrieve_not_found(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": []}

        result = await provider.retrieve(namespace="ns", key="missing")
        assert result is None
        provider._client.get_all.assert_called_with(filters={"user_id": "ns"})

    @pytest.mark.asyncio
    async def test_search(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.search.return_value = {
            "results": [
                {
                    "id": "mem-1",
                    "memory": "result",
                    "metadata": {"_agentic_key": "k1"},
                    "score": 0.9,
                    "created_at": None,
                    "updated_at": None,
                },
            ]
        }

        results = await provider.search(namespace="ns", query="test", top_k=5)

        assert len(results) == 1
        assert results[0].score == 0.9
        provider._client.search.assert_called_once_with("test", filters={"user_id": "ns"}, limit=5)

    @pytest.mark.asyncio
    async def test_search_list_format(self, mock_get_creds):
        """Test when search returns a list instead of dict."""
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.search.return_value = [
            {"id": "mem-1", "memory": "result", "metadata": {}, "score": 0.8, "created_at": None, "updated_at": None},
        ]

        results = await provider.search(namespace="ns", query="test")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_delete_success(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": [{"id": "mem-1", "metadata": {"_agentic_key": "k1"}}]}

        result = await provider.delete(namespace="ns", key="k1")

        assert result is True
        provider._client.delete.assert_called_once_with("mem-1")

    @pytest.mark.asyncio
    async def test_delete_not_found(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": []}

        result = await provider.delete(namespace="ns", key="missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_removes_duplicate_keys_beyond_mem0_default_window(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider()
        entries = [{"id": f"other-{i}", "metadata": {"_agentic_key": "other"}} for i in range(25)]
        entries.insert(0, {"id": "first", "metadata": {"_agentic_key": "target"}})
        entries.append({"id": "last", "metadata": {"_agentic_key": "target"}})

        def get_all(*, filters, top_k):
            assert filters == {"user_id": "ns"}
            return {"results": entries[:top_k]}

        def delete(memory_id):
            entries[:] = [entry for entry in entries if entry["id"] != memory_id]

        provider._client.get_all.side_effect = get_all
        provider._client.delete.side_effect = delete

        assert await provider.delete("ns", "target") is True
        assert {entry["id"] for entry in entries} == {f"other-{i}" for i in range(25)}
        assert {call.args[0] for call in provider._client.delete.call_args_list} == {"first", "last"}

    @pytest.mark.asyncio
    async def test_delete_scans_all_milvus_batches(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(vector_store={"provider": "milvus", "config": {}})
        vector_store = provider._client.vector_store
        vector_store.collection_name = "memory"
        iterator = vector_store.client.query_iterator.return_value
        iterator.next.side_effect = [
            [{"id": f"target-{i}", "metadata": {"user_id": "ns", "_agentic_key": "target"}} for i in range(20)],
            [
                {"id": "wrong-ns", "metadata": {"user_id": "different", "_agentic_key": "target"}},
                {"id": "last", "metadata": {"user_id": "ns", "_agentic_key": "target"}},
            ],
            [],
        ]

        assert await provider.delete("ns", "target") is True
        vector_store.client.query_iterator.assert_called_once_with(
            collection_name="memory",
            filter='metadata["user_id"] == "ns" and metadata["_agentic_key"] == "target"',
            output_fields=["id", "metadata"],
        )
        assert {call.args[0] for call in provider._client.delete.call_args_list} == {
            f"target-{i}" for i in range(20)
        } | {"last"}
        iterator.close.assert_called_once()
        provider._client.get_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_quotes_milvus_filter_values(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(vector_store={"provider": "milvus", "config": {}})
        provider._client.vector_store.client.query_iterator.return_value.next.return_value = []

        assert await provider.delete('ns" or true', 'key" or true') is False
        assert provider._client.vector_store.client.query_iterator.call_args.kwargs["filter"] == (
            'metadata["user_id"] == "ns\\" or true" and metadata["_agentic_key"] == "key\\" or true"'
        )

    @pytest.mark.asyncio
    async def test_delete_closes_milvus_iterator_on_failure(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider(vector_store={"provider": "milvus", "config": {}})
        iterator = provider._client.vector_store.client.query_iterator.return_value
        iterator.next.side_effect = RuntimeError("query failed")

        with pytest.raises(RuntimeError, match="query failed"):
            await provider.delete("ns", "key")
        iterator.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_memories(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {
            "results": [
                {
                    "id": "1",
                    "memory": "mem-a",
                    "metadata": {"_agentic_key": "ka"},
                    "created_at": None,
                    "updated_at": None,
                },
                {
                    "id": "2",
                    "memory": "mem-b",
                    "metadata": {"_agentic_key": "kb", "cat": "X"},
                    "created_at": None,
                    "updated_at": None,
                },
            ]
        }

        records = await provider.list_memories(namespace="ns")
        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_list_memories_with_filter(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {
            "results": [
                {
                    "id": "1",
                    "memory": "a",
                    "metadata": {"_agentic_key": "ka", "cat": "A"},
                    "created_at": None,
                    "updated_at": None,
                },
                {
                    "id": "2",
                    "memory": "b",
                    "metadata": {"_agentic_key": "kb", "cat": "B"},
                    "created_at": None,
                    "updated_at": None,
                },
            ]
        }

        records = await provider.list_memories(namespace="ns", filters={"cat": "A"})
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_list_memories_with_offset_limit(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {
            "results": [
                {"id": str(i), "memory": f"m{i}", "metadata": {}, "created_at": None, "updated_at": None}
                for i in range(5)
            ]
        }

        records = await provider.list_memories(namespace="ns", limit=2, offset=1)
        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_healthcheck_success(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": []}

        with patch("agentic_primitives_gateway.context._server_credentials_allowed", return_value=True):
            assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_healthcheck_failure(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider()
        provider._client.get_all.side_effect = Exception("connection failed")

        with patch("agentic_primitives_gateway.context._server_credentials_allowed", return_value=True):
            assert await provider.healthcheck() is False

    @pytest.mark.asyncio
    async def test_to_record_with_timestamps(self, mock_get_creds):
        entry = {
            "memory": "test",
            "metadata": {"_agentic_key": "k"},
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-02T00:00:00",
        }
        record = Mem0MemoryProvider._to_record("ns", "k", entry)
        assert record.created_at.year == 2025
        assert record.updated_at.day == 2
