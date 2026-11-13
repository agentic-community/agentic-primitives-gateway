from __future__ import annotations

import copy
import json
import logging
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any, TypeVar

from mem0 import Memory

from agentic_primitives_gateway.context import get_boto3_session
from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.memory.base import MemoryProvider

logger = logging.getLogger(__name__)

_T = TypeVar("_T")
_BEDROCK_RUNTIME = "bedrock-runtime"
# Explicitly permitted mem0 providers. Unknown providers, including custom
# wrappers around AWS SDKs, must not inherit the gateway's ambient identity.
# Bridge providers (e.g. langchain / litellm) are excluded: they can hide AWS
# clients in provider-specific configuration that we cannot rebind.
_NON_AWS_PROVIDERS = frozenset(
    {
        "anthropic",
        "azure_ai_search",
        "azure_mysql",
        "azure_openai",
        "azure_openai_structured",
        "baidu",
        "cassandra",
        "chroma",
        "cohere",
        "databricks",
        "deepseek",
        "elasticsearch",
        "faiss",
        "fastembed",
        "gemini",
        "groq",
        "huggingface",
        "lmstudio",
        "milvus",
        "minimax",
        "mongodb",
        "ollama",
        "openai",
        "openai_structured",
        "opensearch",
        "pgvector",
        "pinecone",
        "qdrant",
        "redis",
        "sarvam",
        "sentence_transformer",
        "supabase",
        "together",
        "turbopuffer",
        "upstash_vector",
        "valkey",
        "vertexai",
        "vertex_ai_vector_search",
        "vllm",
        "weaviate",
        "xai",
        "zero_entropy",
        "llm_reranker",
        "huggingface_reranker",
        "neo4j",
    }
)
# Only these AWS components can be rebound to the requesting user's session.
_REBINDABLE = frozenset({("llm",), ("embedder",)})
_CRED_FIELDS = ("aws_access_key_id", "aws_secret_access_key", "aws_session_token")


def _configured_providers(node: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    if isinstance(node, dict):
        if "provider" in node:
            yield path, node["provider"]
        for key, value in node.items():
            yield from _configured_providers(value, (*path, str(key)))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _configured_providers(value, (*path, str(i)))


def _check_supported_aws_components(config: dict[str, Any]) -> bool:
    """Reject providers that may use AWS outside request-bound Bedrock clients."""
    providers = list(_configured_providers(config))
    unsupported = [
        ".".join(path) or "<root>"
        for path, provider in providers
        if not (provider == "aws_bedrock" and path in _REBINDABLE)
        and (not isinstance(provider, str) or provider not in _NON_AWS_PROVIDERS)
    ]
    if unsupported:
        raise ValueError(
            "Mem0MemoryProvider can only bind Bedrock `llm` / `embedder` components to per-request AWS "
            "credentials; refusing AWS-backed component(s) or unknown provider(s) that may use the "
            f"gateway's own identity: {', '.join(unsupported)}"
        )
    return any(provider == "aws_bedrock" for _, provider in providers)


def _bedrock_components(memory: Any) -> list[Any]:
    """mem0 components (LLM / embedder, incl. graph memory) backed by a Bedrock runtime client.

    mem0's Bedrock LLM and embedder build their ``boto3`` client once, at
    ``Memory.from_config`` time, from the process environment — so whatever
    credentials were in the environment then would be used for every later
    call, for every user.  Each operation instead rebinds these clients to
    the request's mode-aware session (see ``Mem0MemoryProvider._bound``).
    """
    found: list[Any] = []
    for owner in (memory, getattr(memory, "graph", None)):
        if owner is None:
            continue
        for attr in ("llm", "embedding_model"):
            component = getattr(owner, attr, None)
            meta = getattr(getattr(component, "client", None), "meta", None)
            if meta is not None and getattr(meta.service_model, "service_name", None) == _BEDROCK_RUNTIME:
                found.append(component)
    return found


class Mem0MemoryProvider(SyncRunnerMixin, MemoryProvider):
    """Memory provider backed by mem0 framework with Milvus vector store.

    A single shared mem0 Memory instance is created at first use and reused
    across requests.  Its Bedrock LLM/embedder clients are rebound to the
    request's ``get_boto3_session()`` — the caller's AWS credentials, or the
    server chain as ``allow_server_credentials`` permits — for each
    operation.  The process environment is never mutated.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider
        config:
          vector_store:
            provider: milvus
            config:
              collection_name: agentic_memories_bedrock
              url: http://localhost:19530
              token: ""
          llm:
            provider: aws_bedrock
            config:
              model: us.anthropic.claude-sonnet-4-20250514-v1:0
          embedder:
            provider: aws_bedrock
            config:
              model: amazon.titan-embed-text-v2:0
    """

    _KEY_FIELD = "_agentic_key"

    def __init__(self, **kwargs: Any) -> None:
        self._mem0_config = kwargs
        self._uses_bedrock = _check_supported_aws_components(kwargs)
        self._client: Any = None
        self._client_lock = threading.Lock()
        # Bedrock operations serialize client rebinding. Non-Bedrock reads
        # remain concurrent while writes serialize their lookup + mutation.
        self._op_lock = threading.Lock()
        logger.info("Mem0 memory provider initialized (lazy client creation)")

    def _get_client(self, session: Any = None) -> Any:
        """Get or create the shared mem0 Memory client (blocking — worker thread).

        mem0's Bedrock LLM makes AWS calls while being constructed (model
        listing), so construction uses the *requesting* caller's session
        credentials rather than the process environment; they're scrubbed
        from the component configs afterwards so no caller secret lingers in
        the shared instance.
        """
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    config = copy.deepcopy({"version": "v1.1", **self._mem0_config})
                    frozen = session.get_credentials().get_frozen_credentials() if session is not None else None
                    if frozen is not None:
                        for path in _REBINDABLE:
                            component = config.get(path[0])
                            if isinstance(component, dict) and component.get("provider") == "aws_bedrock":
                                cfg = component.setdefault("config", {})
                                cfg["aws_access_key_id"] = frozen.access_key
                                cfg["aws_secret_access_key"] = frozen.secret_key
                                if path == ("llm",):  # mem0's embedder config has no token field
                                    cfg["aws_session_token"] = frozen.token
                    memory = None
                    try:
                        memory = Memory.from_config(config)
                    finally:
                        # mem0 retains a parsed copy as memory.config as well as
                        # each component.config. Clear all copies, including the
                        # temporary input when construction raises.
                        for name in ("llm", "embedder"):
                            component = config.get(name)
                            if isinstance(component, dict) and isinstance(component.get("config"), dict):
                                for field in _CRED_FIELDS:
                                    component["config"].pop(field, None)
                            if memory is not None:
                                retained = getattr(getattr(memory, "config", None), name, None)
                                retained_config = getattr(retained, "config", None)
                                if isinstance(retained_config, dict):
                                    for field in _CRED_FIELDS:
                                        retained_config.pop(field, None)
                        if memory is not None:
                            for component in (getattr(memory, "llm", None), getattr(memory, "embedding_model", None)):
                                component_config = getattr(component, "config", None)
                                for field in _CRED_FIELDS:
                                    if isinstance(component_config, dict):
                                        component_config.pop(field, None)
                                    elif hasattr(component_config, field):
                                        setattr(component_config, field, None)
                    self._client = memory
        return self._client

    def _bound(self, fn: Callable[[Any], _T], *, write: bool = False) -> Callable[[], _T]:
        """Bind Bedrock to this request; serialize writes even without Bedrock.

        The lock lives in the worker, so cancellation of an awaiting coroutine
        cannot release it before mem0 completes the lookup and mutation.
        """
        session = get_boto3_session() if self._uses_bedrock else None

        def run() -> _T:
            client = self._get_client(session)
            if session is None and not write:
                return fn(client)
            with self._op_lock:
                if session is not None:
                    for component in _bedrock_components(client):
                        component.client = session.client(
                            _BEDROCK_RUNTIME,
                            region_name=component.client.meta.region_name,
                            config=component.client.meta.config,
                        )
                return fn(client)

        return run

    async def store(
        self,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        store_metadata = {**(metadata or {}), self._KEY_FIELD: key}

        def _store(client: Any) -> None:
            existing = self._find_by_key_sync(client, namespace, key)
            if existing:
                client.update(existing["id"], data=content)
            else:
                client.add(
                    content,
                    user_id=namespace,
                    metadata=store_metadata,
                    infer=False,
                )

        await self._run_sync(self._bound(_store, write=True))

        now = datetime.now(UTC)
        return MemoryRecord(
            namespace=namespace,
            key=key,
            content=content,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )

    async def retrieve(self, namespace: str, key: str) -> MemoryRecord | None:
        entry = await self._run_sync(self._bound(lambda client: self._find_by_key_sync(client, namespace, key)))
        if entry is None:
            return None
        return self._to_record(namespace, key, entry)

    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        results = await self._run_sync(
            self._bound(lambda client: client.search(query, filters={"user_id": namespace}, limit=top_k))
        )

        search_results: list[SearchResult] = []
        for entry in results.get("results", results) if isinstance(results, dict) else results:
            record = self._to_record(
                namespace,
                entry.get("metadata", {}).get(self._KEY_FIELD, entry.get("id", "")),
                entry,
            )
            score = entry.get("score", 0.0)
            search_results.append(SearchResult(record=record, score=score))

        return search_results[:top_k]

    async def delete(self, namespace: str, key: str) -> bool:
        def _delete(client: Any) -> bool:
            deleted = False

            def delete_id(memory_id: str) -> None:
                nonlocal deleted
                client.delete(memory_id)
                deleted = True

            if self._mem0_config.get("vector_store", {}).get("provider") == "milvus":
                # mem0.get_all has no cursor and defaults to just 20 rows.
                # QueryIterator scans all matching Milvus rows regardless of size.
                vector_store = client.vector_store
                iterator = vector_store.client.query_iterator(
                    collection_name=vector_store.collection_name,
                    filter=(
                        f'metadata["user_id"] == {json.dumps(namespace)} '
                        f'and metadata["{self._KEY_FIELD}"] == {json.dumps(key)}'
                    ),
                    output_fields=["id", "metadata"],
                )
                try:
                    while rows := iterator.next():
                        for row in rows:
                            metadata = row.get("metadata") or {}
                            if metadata.get("user_id") == namespace and metadata.get(self._KEY_FIELD) == key:
                                delete_id(row["id"])
                finally:
                    iterator.close()
            else:
                # Other mem0 stores expose only get_all(top_k), not offset.
                # Grow the window until it contains the entire namespace.
                top_k = 20
                while True:
                    all_memories = client.get_all(filters={"user_id": namespace}, top_k=top_k)
                    entries = (
                        all_memories.get("results", all_memories) if isinstance(all_memories, dict) else all_memories
                    )
                    if len(entries) < top_k:
                        break
                    top_k *= 2
                for entry in entries:
                    if entry.get("metadata", {}).get(self._KEY_FIELD) == key:
                        delete_id(entry["id"])

            return deleted

        result: bool = await self._run_sync(self._bound(_delete, write=True))
        return result

    async def list_memories(
        self,
        namespace: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        all_memories = await self._run_sync(self._bound(lambda client: client.get_all(filters={"user_id": namespace})))

        entries = all_memories.get("results", all_memories) if isinstance(all_memories, dict) else all_memories
        records: list[MemoryRecord] = []
        for entry in entries:
            meta = entry.get("metadata", {})
            if filters and not all(meta.get(k) == v for k, v in filters.items()):
                continue
            record = self._to_record(
                namespace,
                meta.get(self._KEY_FIELD, entry.get("id", "")),
                entry,
            )
            records.append(record)

        return records[offset : offset + limit]

    async def healthcheck(self) -> bool | str:
        # Readiness has no caller AWS identity. Do not initialize Bedrock mem0
        # (its constructor calls AWS) or use a previously caller-bound client.
        if self._uses_bedrock:
            vector = self._mem0_config.get("vector_store", {})
            if vector.get("provider") != "milvus":
                return "reachable"  # AWS and other stores are not checked here

            def check_milvus() -> bool:
                from pymilvus import MilvusClient  # type: ignore[import-untyped]

                config = vector.get("config", {})
                client = MilvusClient(uri=config.get("url", "http://localhost:19530"), token=config.get("token"))
                try:
                    return bool(client.has_collection(config.get("collection_name", "mem0")))
                finally:
                    client.close()

            try:
                if await self._run_sync(check_milvus):
                    return "reachable"  # Milvus checked; AWS credentials still unknown
                return False
            except Exception:
                logger.exception("Mem0 Milvus healthcheck failed")
                return False
        try:
            await self._run_sync(self._bound(lambda client: client.get_all(filters={"user_id": "__healthcheck__"})))
            return True
        except Exception:
            logger.exception("Mem0 healthcheck failed")
            return False

    @staticmethod
    def _find_by_key_sync(client: Any, namespace: str, key: str) -> dict[str, Any] | None:
        all_memories = client.get_all(filters={"user_id": namespace})
        entries = all_memories.get("results", all_memories) if isinstance(all_memories, dict) else all_memories
        for entry in entries:
            if entry.get("metadata", {}).get(Mem0MemoryProvider._KEY_FIELD) == key:
                result: dict[str, Any] = entry
                return result
        return None

    @staticmethod
    def _to_record(namespace: str, key: str, entry: dict[str, Any]) -> MemoryRecord:
        meta = dict(entry.get("metadata", {}))
        meta.pop(Mem0MemoryProvider._KEY_FIELD, None)

        created = entry.get("created_at")
        updated = entry.get("updated_at")
        now = datetime.now(UTC)

        return MemoryRecord(
            namespace=namespace,
            key=key,
            content=entry.get("memory", entry.get("data", "")),
            metadata=meta,
            created_at=datetime.fromisoformat(created) if isinstance(created, str) else now,
            updated_at=datetime.fromisoformat(updated) if isinstance(updated, str) else now,
        )
