from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import partial
from typing import Any

from mem0 import Memory

from agentic_primitives_gateway.context import get_aws_credentials
from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult
from agentic_primitives_gateway.primitives.memory.base import MemoryProvider

logger = logging.getLogger(__name__)

# Lock to prevent concurrent env var mutation across threads
_env_lock = threading.Lock()


@contextmanager
def _with_aws_env(creds: Any) -> Iterator[None]:
    """Temporarily inject AWS credentials into env vars.

    mem0's Bedrock LLM/embedder reads credentials from the environment.
    This sets them for the duration of the operation and restores originals
    after. Thread-safe via a lock. The creds object must be resolved from
    contextvars BEFORE entering the thread pool.
    """
    if creds is None:
        from agentic_primitives_gateway.context import _server_credentials_allowed

        if not _server_credentials_allowed():
            raise ValueError(
                "No AWS credentials provided in request headers and server "
                "credential fallback is disabled. Either pass credentials via "
                "X-AWS-* headers from the client, or enable server credentials "
                "with allow_server_credentials: true in the server config."
            )
        yield
        return

    env_pairs = [
        ("AWS_ACCESS_KEY_ID", creds.access_key_id),
        ("AWS_SECRET_ACCESS_KEY", creds.secret_access_key),
        ("AWS_SESSION_TOKEN", creds.session_token),
        ("AWS_REGION", creds.region),
        ("AWS_DEFAULT_REGION", creds.region),
    ]

    with _env_lock:
        saved: dict[str, str | None] = {}
        try:
            for key, value in env_pairs:
                if value:
                    saved[key] = os.environ.get(key)
                    os.environ[key] = value
            yield
        finally:
            for key, original in saved.items():
                if original is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = original


class Mem0MemoryProvider(MemoryProvider):
    """Memory provider backed by mem0 framework with Milvus vector store.

    A single shared mem0 Memory instance is created at first use and reused
    across requests. AWS credentials for the Bedrock LLM/embedder are
    injected into the environment before each operation so the caller's
    identity is used.

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
        self._client: Any = None
        self._client_lock = threading.Lock()
        logger.info("Mem0 memory provider initialized (lazy client creation)")

    def _get_client(self, creds: Any = None) -> Any:
        """Get or create the shared mem0 Memory client."""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    with _with_aws_env(creds):
                        self._client = Memory.from_config({"version": "v1.1", **self._mem0_config})
        return self._client

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    @staticmethod
    def _resolve_creds() -> Any:
        """Resolve AWS creds from context. Must be called from async context."""
        return get_aws_credentials()

    async def store(
        self,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        creds = self._resolve_creds()
        client = self._get_client(creds)
        store_metadata = {**(metadata or {}), self._KEY_FIELD: key}

        def _store():
            with _with_aws_env(creds):
                existing = self._find_by_key_sync(client, namespace, key)
                if existing:
                    client.update(existing["id"], data=content)
                else:
                    client.add(
                        content,
                        user_id=namespace,
                        metadata=store_metadata,
                    )

        await self._run_sync(_store)

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
        creds = self._resolve_creds()
        client = self._get_client(creds)

        def _retrieve():
            with _with_aws_env(creds):
                return self._find_by_key_sync(client, namespace, key)

        entry = await self._run_sync(_retrieve)
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
        creds = self._resolve_creds()
        client = self._get_client(creds)

        def _search():
            with _with_aws_env(creds):
                return client.search(query, user_id=namespace, limit=top_k)

        results = await self._run_sync(_search)

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
        creds = self._resolve_creds()
        client = self._get_client(creds)

        def _delete():
            with _with_aws_env(creds):
                entry = self._find_by_key_sync(client, namespace, key)
                if entry is None:
                    return False
                client.delete(entry["id"])
                return True

        result: bool = await self._run_sync(_delete)
        return result

    async def list_memories(
        self,
        namespace: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        creds = self._resolve_creds()
        client = self._get_client(creds)

        def _list():
            with _with_aws_env(creds):
                return client.get_all(user_id=namespace)

        all_memories = await self._run_sync(_list)

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

    async def healthcheck(self) -> bool:
        try:
            client = self._get_client()
            await self._run_sync(client.get_all, user_id="__healthcheck__")
            return True
        except Exception:
            logger.exception("Mem0 healthcheck failed")
            return False

    @staticmethod
    def _find_by_key_sync(client: Any, namespace: str, key: str) -> dict[str, Any] | None:
        all_memories = client.get_all(user_id=namespace)
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
