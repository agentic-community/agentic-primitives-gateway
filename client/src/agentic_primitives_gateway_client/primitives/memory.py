"""Memory helper for the Agentic Primitives Gateway client.

Provides two usage patterns:

1. **Auto-memory** (transparent) — store every conversation turn and
   retrieve context automatically. The memory backend decides what to
   extract and compact.

2. **Explicit memory tools** — remember, recall, search, list, forget
   as methods that can be wrapped in any agent framework's ``@tool``
   decorator.

Optionally integrates with :class:`Observability` to trace memory operations.

Usage (async)::

    from agentic_primitives_gateway_client import AgenticPlatformClient, Memory

    client = AgenticPlatformClient("http://localhost:8000", ...)
    memory = Memory(client, namespace="agent:my-agent")

    # Auto-memory
    await memory.store_turn("user", user_input)
    context = await memory.recall_context(user_input)

    # Explicit tools (wrap in your framework's @tool)
    result = await memory.remember("api-limit", "100 req/min", source="docs")
    result = await memory.search("rate limiting")

Usage (sync, for Strands etc.)::

    memory.store_turn_sync("user", user_input)
    context = memory.recall_context_sync(user_input)
    result = memory.remember_sync("api-limit", "100 req/min")
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agentic_primitives_gateway_client.client import AgenticPlatformClient

if TYPE_CHECKING:
    from agentic_primitives_gateway_client.primitives.observability import Observability

logger = logging.getLogger(__name__)


class Memory:
    """Memory helper backed by the Agentic Primitives Gateway.

    Provides transparent conversation persistence (store_turn / recall_context)
    and explicit memory CRUD (remember / recall / search / list / forget).
    """

    def __init__(
        self,
        client: AgenticPlatformClient,
        namespace: str,
        session_id: str | None = None,
        top_k: int = 10,
        observability: Observability | None = None,
    ) -> None:
        self._client = client
        self.namespace = namespace
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.top_k = top_k
        self._obs = observability
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Auto-memory (transparent) ───────────────────────────────────

    async def store_turn(self, role: str, content: str) -> None:
        """Store a conversation turn. The memory backend decides what to extract."""
        turn_key = hashlib.sha256(
            f"{self.session_id}:{role}:{content[:100]}:{datetime.now(UTC).isoformat()}".encode()
        ).hexdigest()[:12]

        try:
            await self._client.store_memory(
                self.namespace,
                key=f"turn-{turn_key}",
                content=f"[{role}] {content}",
                metadata={
                    "session_id": self.session_id,
                    "role": role,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )
            if self._obs:
                await self._obs.trace(
                    "memory:store_turn",
                    {"role": role, "content": content[:100]},
                    f"Stored turn as turn-{turn_key}",
                )
        except Exception as e:
            logger.debug("memory store failed: %s", e)

    async def recall_context(self, query: str, top_k: int | None = None) -> str:
        """Retrieve context: recent conversation turns + semantic search."""
        k = top_k or self.top_k
        sections: list[str] = []

        try:
            result = await self._client.list_memories(self.namespace, limit=k)
            records = result.get("records", [])
            if records:
                lines = [r["content"] for r in records]
                sections.append("Recent conversation:\n" + "\n".join(lines))
        except Exception as e:
            logger.debug("memory list failed: %s", e)

        try:
            result = await self._client.search_memory(
                self.namespace,
                query=query,
                top_k=k,
            )
            hits = result.get("results", [])
            if hits:
                lines = [h["record"]["content"] for h in hits]
                sections.append("Related memories:\n" + "\n".join(lines))
        except Exception as e:
            logger.debug("memory search failed: %s", e)

        context = "\n\n".join(sections)
        if self._obs and context:
            await self._obs.trace(
                "memory:recall_context",
                {"query": query, "top_k": k},
                context[:500],
            )
        return context

    # ── Explicit memory tools (async) ───────────────────────────────

    async def remember(self, key: str, content: str, source: str = "") -> str:
        """Store a piece of information in memory."""
        metadata = {"source": source} if source else {}
        result = await self._client.store_memory(self.namespace, key, content, metadata=metadata)
        output = f"Stored memory '{result['key']}'"
        if self._obs:
            await self._obs.trace("remember", {"key": key, "content": content[:100]}, output)
        return output

    async def recall(self, key: str) -> str:
        """Retrieve a specific memory by key."""
        try:
            result = await self._client.retrieve_memory(self.namespace, key)
            output = f"[{result['key']}] {result['content']} (metadata: {result['metadata']})"
            if self._obs:
                await self._obs.trace("memory:recall", {"key": key}, output[:200])
            return output
        except Exception as e:
            return f"Memory '{key}' not found: {e}"

    async def search(self, query: str, top_k: int = 5) -> str:
        """Search memory for relevant information."""
        result = await self._client.search_memory(self.namespace, query=query, top_k=top_k)
        hits = result.get("results", [])
        if not hits:
            output = "No relevant memories found."
        else:
            lines = [f"  [{h['record']['key']}] (score: {h['score']:.2f}) {h['record']['content']}" for h in hits]
            output = f"Found {len(hits)} memories:\n" + "\n".join(lines)
        if self._obs:
            await self._obs.trace("search_memory", {"query": query, "top_k": top_k}, output)
        return output

    async def list(self, limit: int = 20) -> str:
        """List all stored memories."""
        result = await self._client.list_memories(self.namespace, limit=limit)
        records = result.get("records", [])
        if not records:
            return "No memories stored yet."
        lines = [f"  [{r['key']}] {r['content'][:80]}" for r in records]
        output = f"{result['total']} memories:\n" + "\n".join(lines)
        if self._obs:
            await self._obs.trace("memory:list", {"limit": limit}, f"{len(records)} records")
        return output

    async def forget(self, key: str) -> str:
        """Delete a specific memory."""
        try:
            await self._client.delete_memory(self.namespace, key)
            output = f"Deleted memory '{key}'"
            if self._obs:
                await self._obs.trace("forget", {"key": key}, output)
            return output
        except Exception as e:
            return f"Could not delete '{key}': {e}"

    # ── Sync wrappers ───────────────────────────────────────────────

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _sync(self, coro: Any) -> Any:
        return self._get_loop().run_until_complete(coro)

    def store_turn_sync(self, role: str, content: str) -> None:
        self._sync(self.store_turn(role, content))

    def recall_context_sync(self, query: str, top_k: int | None = None) -> str:
        return str(self._sync(self.recall_context(query, top_k)))

    def remember_sync(self, key: str, content: str, source: str = "") -> str:
        return str(self._sync(self.remember(key, content, source)))

    def recall_sync(self, key: str) -> str:
        return str(self._sync(self.recall(key)))

    def search_sync(self, query: str, top_k: int = 5) -> str:
        return str(self._sync(self.search(query, top_k)))

    def list_sync(self, limit: int = 20) -> str:
        return str(self._sync(self.list(limit)))

    def forget_sync(self, key: str) -> str:
        return str(self._sync(self.forget(key)))
