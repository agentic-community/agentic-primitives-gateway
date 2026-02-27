from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from bedrock_agentcore.memory import MemorySessionManager
from bedrock_agentcore.memory.constants import ConversationalMessage, MessageRole

from agentic_primitives_gateway.context import get_boto3_session, get_service_credentials
from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.memory.base import MemoryProvider

logger = logging.getLogger(__name__)


class AgentCoreMemoryProvider(SyncRunnerMixin, MemoryProvider):
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
        # Write-through cache: (namespace, key) -> MemoryRecord.
        # AgentCore memory is conversational — there's no native key-value
        # store.  This cache bridges the KV abstraction so that store/retrieve
        # /delete work within the lifetime of the provider instance, while
        # also persisting content as conversation turns in AgentCore.
        self._kv_cache: dict[tuple[str, str], MemoryRecord] = {}
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
        record = MemoryRecord(
            namespace=namespace,
            key=key,
            content=content,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        self._kv_cache[(namespace, key)] = record
        return record

    async def retrieve(self, namespace: str, key: str) -> MemoryRecord | None:
        # Check local KV cache first
        cached = self._kv_cache.get((namespace, key))
        if cached is not None:
            return cached
        # Fall back to searching AgentCore long-term + short-term memories
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
                    content=str(entry_dict.get("memory") or entry_dict.get("content") or ""),
                    metadata=meta,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                score = float(entry_dict.get("score") or entry_dict.get("relevance_score") or 0.0)
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

        # 3. Include matching entries from the local KV cache
        #    Use word-level matching as a basic approximation of semantic
        #    search: any significant query word sharing a 4-char prefix with
        #    a content word is considered a match (simple stemming heuristic).
        query_words = {w for w in query.lower().split() if len(w) > 2}
        for (ns, _key), rec in self._kv_cache.items():
            if ns != namespace:
                continue
            content_words = set(rec.content.lower().split())
            if any(qw[:4] == cw[:4] for qw in query_words for cw in content_words if len(cw) > 2):
                search_results.append(SearchResult(record=rec, score=0.8))

        # Dedupe by content, sort by score
        seen: set[str] = set()
        deduped: list[SearchResult] = []
        for r in sorted(search_results, key=lambda x: x.score, reverse=True):
            if r.record.content not in seen:
                seen.add(r.record.content)
                deduped.append(r)
        return deduped[:top_k]

    async def delete(self, namespace: str, key: str) -> bool:
        # Remove from local KV cache
        cache_hit = self._kv_cache.pop((namespace, key), None)

        # Also attempt to remove from AgentCore long-term memories
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
            sdk_deleted: bool = await self._run_sync(_delete)
            return sdk_deleted or cache_hit is not None
        except Exception:
            logger.debug("Delete failed", exc_info=True)
            # Even if SDK delete fails, cache removal counts
            return cache_hit is not None

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
                        content=str(entry_dict.get("memory") or entry_dict.get("content") or ""),
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

        # Include cached KV entries for this namespace
        seen_content: set[str] = {r.content for r in records}
        for (ns, _key), rec in self._kv_cache.items():
            if ns != namespace:
                continue
            if filters and not all(rec.metadata.get(k) == v for k, v in filters.items()):
                continue
            if rec.content not in seen_content:
                records.append(rec)
                seen_content.add(rec.content)

        return records[offset : offset + limit]

    # ── Conversation memory ──────────────────────────────────────────

    @staticmethod
    def _normalize_event(
        raw: Any,
        *,
        actor_id: str,
        session_id: str,
        messages: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Normalize an SDK event result to match the EventInfo model shape."""
        if isinstance(raw, dict):
            return {
                "event_id": raw.get("event_id") or raw.get("eventId", ""),
                "actor_id": raw.get("actor_id") or raw.get("actorId", actor_id),
                "session_id": raw.get("session_id") or raw.get("sessionId", session_id),
                "messages": raw.get("messages", messages or []),
                "metadata": raw.get("metadata", {}),
            }
        return {
            "event_id": str(getattr(raw, "event_id", getattr(raw, "eventId", ""))),
            "actor_id": actor_id,
            "session_id": session_id,
            "messages": messages or [],
            "metadata": {},
        }

    async def create_event(
        self,
        actor_id: str,
        session_id: str,
        messages: list[tuple[str, str]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()

        def _create():
            manager = self._make_manager(memory_id, boto_session)
            session = manager.create_memory_session(
                actor_id=actor_id,
                session_id=session_id,
            )
            conv_messages = [ConversationalMessage(text=text, role=MessageRole(role)) for text, role in messages]
            return session.add_turns(messages=conv_messages)

        result = await self._run_sync(_create)
        msg_dicts = [{"text": t, "role": r} for t, r in messages]
        normalized = self._normalize_event(
            result,
            actor_id=actor_id,
            session_id=session_id,
            messages=msg_dicts,
        )
        if metadata:
            normalized["metadata"] = metadata
        return normalized

    async def list_events(
        self,
        actor_id: str,
        session_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()

        def _list():
            manager = self._make_manager(memory_id, boto_session)
            return manager.list_events(actor_id=actor_id, session_id=session_id)

        events = await self._run_sync(_list)
        result: list[dict[str, Any]] = []
        for e in events[:limit]:
            result.append(self._normalize_event(e, actor_id=actor_id, session_id=session_id))
        return result

    async def get_event(
        self,
        actor_id: str,
        session_id: str,
        event_id: str,
    ) -> dict[str, Any]:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()

        def _get():
            manager = self._make_manager(memory_id, boto_session)
            return manager.get_event(
                actor_id=actor_id,
                session_id=session_id,
                event_id=event_id,
            )

        event = await self._run_sync(_get)
        return self._normalize_event(
            event,
            actor_id=actor_id,
            session_id=session_id,
        )

    async def delete_event(
        self,
        actor_id: str,
        session_id: str,
        event_id: str,
    ) -> None:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()

        def _delete():
            manager = self._make_manager(memory_id, boto_session)
            manager.delete_event(
                actor_id=actor_id,
                session_id=session_id,
                event_id=event_id,
            )

        await self._run_sync(_delete)

    async def get_last_turns(
        self,
        actor_id: str,
        session_id: str,
        *,
        k: int = 5,
    ) -> list[list[dict[str, str]]]:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()

        def _get_turns():
            manager = self._make_manager(memory_id, boto_session)
            session = manager.create_memory_session(
                actor_id=actor_id,
                session_id=session_id,
            )
            return session.get_last_k_turns(k=k)

        raw_turns = await self._run_sync(_get_turns)
        result: list[list[dict[str, str]]] = []
        for turn_group in raw_turns:
            msgs: list[dict[str, str]] = []
            for msg in turn_group:
                if isinstance(msg, dict):
                    content = msg.get("content", {})
                    text = content.get("text", str(msg)) if isinstance(content, dict) else str(content)
                    role = msg.get("role", "")
                    msgs.append({"text": text, "role": role})
                else:
                    msgs.append({"text": str(msg), "role": ""})
            result.append(msgs)
        return result

    # ── Session management ───────────────────────────────────────────

    async def list_actors(self) -> list[dict[str, Any]]:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()

        def _list():
            manager = self._make_manager(memory_id, boto_session)
            return manager.list_actors()

        actors = await self._run_sync(_list)
        result: list[dict[str, Any]] = []
        for a in actors:
            if isinstance(a, dict):
                result.append(a)
            else:
                result.append({"actor_id": str(a)})
        return result

    async def list_sessions(self, actor_id: str) -> list[dict[str, Any]]:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()

        def _list():
            manager = self._make_manager(memory_id, boto_session)
            return manager.list_actor_sessions(actor_id=actor_id)

        sessions = await self._run_sync(_list)
        result: list[dict[str, Any]] = []
        for s in sessions:
            if isinstance(s, dict):
                result.append(s)
            else:
                result.append({"session_id": str(s), "actor_id": actor_id})
        return result

    # ── Branch management ────────────────────────────────────────────

    async def fork_conversation(
        self,
        actor_id: str,
        session_id: str,
        root_event_id: str,
        branch_name: str,
        messages: list[tuple[str, str]],
    ) -> dict[str, Any]:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()

        def _fork():
            manager = self._make_manager(memory_id, boto_session)
            conv_messages = [ConversationalMessage(text=text, role=MessageRole(role)) for text, role in messages]
            return manager.fork_conversation(
                actor_id=actor_id,
                session_id=session_id,
                root_event_id=root_event_id,
                branch_name=branch_name,
                messages=conv_messages,
            )

        result = await self._run_sync(_fork)
        if isinstance(result, dict):
            return result
        return {
            "name": branch_name,
            "root_event_id": root_event_id,
        }

    async def list_branches(
        self,
        actor_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        memory_id = self._resolve_memory_id()
        boto_session = self._resolve_boto3_session()

        def _list():
            manager = self._make_manager(memory_id, boto_session)
            return manager.list_branches(actor_id=actor_id, session_id=session_id)

        branches = await self._run_sync(_list)
        result: list[dict[str, Any]] = []
        for b in branches:
            if isinstance(b, dict):
                result.append(b)
            else:
                result.append({"name": str(b)})
        return result

    # ── Control plane ────────────────────────────────────────────────

    def _get_control_plane_client(self) -> Any:
        """Get the bedrock-agentcore-control boto3 client for control plane ops."""
        boto_session = self._resolve_boto3_session()
        return boto_session.client(
            "bedrock-agentcore-control",
            region_name=boto_session.region_name,
        )

    async def create_memory_resource(
        self,
        name: str,
        *,
        strategies: list[dict[str, Any]] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        cp = self._get_control_plane_client()

        def _create() -> dict[str, Any]:
            kwargs: dict[str, Any] = {
                "name": name,
                "eventExpiryDuration": 90,  # days — required by API
            }
            if description:
                kwargs["description"] = description
            if strategies:
                kwargs["memoryStrategies"] = strategies
            resp = cp.create_memory(**kwargs)
            mem = resp.get("memory", resp)
            return {
                "memory_id": mem.get("id", ""),
                "name": mem.get("name", name),
                "status": mem.get("status", ""),
                "arn": mem.get("arn", ""),
            }

        return await self._run_sync(_create)

    async def get_memory_resource(self, memory_id: str) -> dict[str, Any]:
        cp = self._get_control_plane_client()

        def _get() -> dict[str, Any]:
            resp = cp.get_memory(memoryId=memory_id)
            mem = resp.get("memory", resp)
            return {
                "memory_id": mem.get("id", memory_id),
                "name": mem.get("name", ""),
                "status": mem.get("status", ""),
                "arn": mem.get("arn", ""),
            }

        return await self._run_sync(_get)

    async def list_memory_resources(self) -> list[dict[str, Any]]:
        cp = self._get_control_plane_client()

        def _list() -> list[dict[str, Any]]:
            resp = cp.list_memories()
            result: list[dict[str, Any]] = []
            for mem in resp.get("memories", []):
                result.append(
                    {
                        "memory_id": mem.get("id", ""),
                        "name": mem.get("name", ""),
                        "status": mem.get("status", ""),
                        "arn": mem.get("arn", ""),
                    }
                )
            return result

        return await self._run_sync(_list)

    async def delete_memory_resource(self, memory_id: str) -> None:
        cp = self._get_control_plane_client()

        def _delete() -> None:
            cp.delete_memory(memoryId=memory_id)

        await self._run_sync(_delete)

    # ── Strategy management ──────────────────────────────────────────

    async def list_strategies(self, memory_id: str) -> list[dict[str, Any]]:
        cp = self._get_control_plane_client()

        def _list() -> list[dict[str, Any]]:
            resp = cp.get_memory(memoryId=memory_id)
            mem = resp.get("memory", resp)
            result: list[dict[str, Any]] = []
            for s in mem.get("strategies", []):
                result.append(
                    {
                        "strategy_id": s.get("strategyId", ""),
                        "name": s.get("name", ""),
                        "description": s.get("description", ""),
                    }
                )
            return result

        return await self._run_sync(_list)

    async def add_strategy(
        self,
        memory_id: str,
        strategy: dict[str, Any],
    ) -> dict[str, Any]:
        cp = self._get_control_plane_client()

        def _add() -> dict[str, Any]:
            resp = cp.update_memory(
                memoryId=memory_id,
                memoryStrategies={
                    "addMemoryStrategies": [strategy],
                },
            )
            mem = resp.get("memory", resp)
            strategies = mem.get("strategies", [])
            # Return the last added strategy
            if strategies:
                last = strategies[-1]
                return {
                    "strategy_id": last.get("strategyId", ""),
                    "name": last.get("name", ""),
                }
            return {"strategy_id": ""}

        return await self._run_sync(_add)

    async def delete_strategy(self, memory_id: str, strategy_id: str) -> None:
        cp = self._get_control_plane_client()

        def _delete() -> None:
            cp.update_memory(
                memoryId=memory_id,
                memoryStrategies={
                    "deleteMemoryStrategies": [{"memoryStrategyId": strategy_id}],
                },
            )

        await self._run_sync(_delete)

    async def healthcheck(self) -> bool:
        # Healthcheck doesn't have request context, just check basic connectivity
        return True
