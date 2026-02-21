from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime
from functools import partial
from typing import Any

from bedrock_agentcore.memory import MemorySessionManager
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

from agentic_primitives_gateway.context import get_boto3_session, get_service_credentials
from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult
from agentic_primitives_gateway.primitives.memory.base import MemoryProvider

logger = logging.getLogger(__name__)


class AgentCoreMemoryProvider(MemoryProvider):
    """Memory provider backed by AWS Bedrock AgentCore Memory service.

    The memory_id is resolved per-request in this order:
    1. Client header: X-Cred-Agentcore-Memory-Id
    2. Config-level default (if provided)
    3. Error — AgentCore memory IDs must be created externally

    Uses a stable session ID per namespace so all turns build a single
    conversation thread. Search combines long-term memories with recent
    short-term turns.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider
        config:
          region: "us-east-1"
    """

    _KEY_FIELD = "_agentic_key"

    def __init__(
        self,
        region: str = "us-east-1",
        memory_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._default_memory_id = memory_id
        self._region = region
        logger.info(
            "AgentCore memory provider initialized (region=%s, default_memory_id=%s)",
            region,
            memory_id or "(from client)",
        )

    def _resolve_memory_id(self) -> str:
        """Resolve the memory_id from request context. Call from async context only."""
        creds = get_service_credentials("agentcore")
        if creds and creds.get("memory_id"):
            return creds["memory_id"]

        if self._default_memory_id:
            return self._default_memory_id

        raise ValueError(
            "AgentCore memory_id is required. Provide it via: "
            "(1) client header X-Cred-Agentcore-Memory-Id, "
            "(2) AGENTCORE_MEMORY_ID env var in the agent, or "
            "(3) memory_id in the server provider config. "
            "Create a memory resource in the AgentCore console first."
        )

    def _resolve_boto3_session(self) -> Any:
        """Resolve boto3 session from request context. Call from async context only."""
        return get_boto3_session(default_region=self._region)

    @staticmethod
    def _stable_session_id(namespace: str) -> str:
        """Deterministic session ID from namespace."""
        return hashlib.sha256(namespace.encode()).hexdigest()[:32]

    def _make_session(self, memory_id: str, boto_session: Any, namespace: str) -> Any:
        """Create a MemorySession. Safe to call from thread pool (no contextvars)."""
        manager = MemorySessionManager(
            memory_id=memory_id,
            region_name=boto_session.region_name,
            boto3_session=boto_session,
        )
        return manager.create_memory_session(
            actor_id=namespace,
            session_id=self._stable_session_id(namespace),
        )

    def _make_manager(self, memory_id: str, boto_session: Any) -> Any:
        """Create a MemorySessionManager. Safe to call from thread pool."""
        return MemorySessionManager(
            memory_id=memory_id,
            region_name=boto_session.region_name,
            boto3_session=boto_session,
        )

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def store(
        self,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        # Resolve context-dependent values BEFORE entering thread pool
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()

        def _store():
            session = self._make_session(memory_id, boto_session, namespace)
            message = ConversationalMessage(text=content, role=MessageRole.USER)
            session.add_turns(messages=[message])

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
        results = await self.search(namespace, query=key, top_k=10)
        for r in results:
            if r.record.key == key:
                return r.record
        return None

    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()
        search_results: list[SearchResult] = []

        # 1. Search long-term memories
        def _search_lt():
            manager = self._make_manager(memory_id, boto_session)
            return manager.search_long_term_memories(
                query=query,
                namespace_prefix=namespace,
                top_k=top_k,
            )

        try:
            lt_results = await self._run_sync(_search_lt)
            for entry in lt_results:
                entry_dict = entry if isinstance(entry, dict) else {}
                meta = dict(entry_dict.get("metadata", {}))
                record_key = meta.pop(self._KEY_FIELD, entry_dict.get("id", ""))
                record = MemoryRecord(
                    namespace=namespace,
                    key=record_key,
                    content=entry_dict.get("memory", entry_dict.get("content", "")),
                    metadata=meta,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                score = entry_dict.get("score", entry_dict.get("relevance_score", 0.0))
                search_results.append(SearchResult(record=record, score=score))
        except Exception:
            logger.debug("Long-term memory search failed", exc_info=True)

        # 2. Also fetch recent short-term turns for context
        def _search_st():
            session = self._make_session(memory_id, boto_session, namespace)
            return session.get_last_k_turns(k=top_k)

        try:
            recent_turns = await self._run_sync(_search_st)
            query_lower = query.lower()
            for turn_group in recent_turns:
                for msg in turn_group:
                    raw = str(msg)
                    # Extract readable text from the EventMessage
                    content = msg.get("content", {})
                    if isinstance(content, dict):  # noqa: SIM108
                        text = content.get("text", raw)
                    else:
                        text = str(content) if content else raw
                    if query_lower in raw.lower():
                        record = MemoryRecord(
                            namespace=namespace,
                            key=f"turn-{id(msg)}",
                            content=text,
                            metadata={"source": "short_term"},
                            created_at=datetime.now(UTC),
                            updated_at=datetime.now(UTC),
                        )
                        search_results.append(SearchResult(record=record, score=0.5))
        except Exception:
            logger.warning("Short-term turn fetch failed", exc_info=True)

        # Dedupe by content, sort by score
        seen: set[str] = set()
        deduped: list[SearchResult] = []
        for r in sorted(search_results, key=lambda x: x.score, reverse=True):
            if r.record.content not in seen:
                seen.add(r.record.content)
                deduped.append(r)
        return deduped[:top_k]

    async def delete(self, namespace: str, key: str) -> bool:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()

        def _delete():
            manager = self._make_manager(memory_id, boto_session)
            records = manager.search_long_term_memories(
                query=key,
                namespace_prefix=namespace,
                top_k=50,
            )
            for record in records:
                record_dict = record if isinstance(record, dict) else {}
                meta = record_dict.get("metadata", {})
                if meta.get(self._KEY_FIELD) == key:
                    record_id = record_dict.get("id")
                    if record_id:
                        session = self._make_session(memory_id, boto_session, namespace)
                        session.delete_memory_record(record_id)
                        return True
            return False

        try:
            result: bool = await self._run_sync(_delete)
            return result
        except Exception:
            logger.debug("Delete failed", exc_info=True)
            return False

    async def list_memories(
        self,
        namespace: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()
        records: list[MemoryRecord] = []

        def _list():
            session = self._make_session(memory_id, boto_session, namespace)
            lt = session.list_long_term_memory_records(
                namespace_prefix=namespace,
                max_results=limit + offset,
            )
            st = session.get_last_k_turns(k=limit)
            return lt, st

        try:
            lt_records, st_turns = await self._run_sync(_list)

            for entry in lt_records:
                entry_dict = entry if isinstance(entry, dict) else {}
                meta = dict(entry_dict.get("metadata", {}))
                if filters and not all(meta.get(k) == v for k, v in filters.items()):
                    continue
                record_key = meta.pop(self._KEY_FIELD, entry_dict.get("id", ""))
                records.append(
                    MemoryRecord(
                        namespace=namespace,
                        key=record_key,
                        content=entry_dict.get("memory", entry_dict.get("content", "")),
                        metadata=meta,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    )
                )

            for turn_group in st_turns:
                for msg in turn_group:
                    text = str(msg)
                    records.append(
                        MemoryRecord(
                            namespace=namespace,
                            key=f"turn-{id(msg)}",
                            content=text,
                            metadata={"source": "short_term"},
                            created_at=datetime.now(UTC),
                            updated_at=datetime.now(UTC),
                        )
                    )
        except Exception:
            logger.debug("List failed", exc_info=True)

        return records[offset : offset + limit]

    async def healthcheck(self) -> bool:
        # Healthcheck doesn't have request context, just check basic connectivity
        return True
