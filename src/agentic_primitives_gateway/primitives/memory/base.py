from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult


class MemoryProvider(ABC):
    """Abstract base class for memory providers.

    Memory providers handle storage, retrieval, and search of agent memories.
    Implementations may use frameworks like mem0 or langmem on top of vector
    stores like Milvus or Weaviate.

    The ABC auto-wraps ``retrieve`` / ``search`` / ``list_memories`` on
    every subclass via ``__init_subclass__`` to strip operator-configured
    ``memory.metadata_denylist`` keys from ``MemoryRecord.metadata``
    before the response leaves the provider.  Subclasses do not scrub
    themselves — the enrichment is inherited.  Same pattern as
    ``KnowledgeProvider``; see ``primitives/_metadata_scrub.py``.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Local import — avoids a top-level cycle between the ABC and
        # the settings graph, which imports later than primitives.
        from agentic_primitives_gateway.primitives.memory._audit import (
            wrap_list_memories,
            wrap_retrieve,
            wrap_search,
        )

        own = cls.__dict__
        if "retrieve" in own:
            cls.retrieve = wrap_retrieve(own["retrieve"])  # type: ignore[method-assign]
        if "search" in own:
            cls.search = wrap_search(own["search"])  # type: ignore[method-assign]
        if "list_memories" in own:
            cls.list_memories = wrap_list_memories(own["list_memories"])  # type: ignore[method-assign]

    @abstractmethod
    async def store(
        self,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord: ...

    @abstractmethod
    async def retrieve(self, namespace: str, key: str) -> MemoryRecord | None: ...

    @abstractmethod
    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]: ...

    @abstractmethod
    async def delete(self, namespace: str, key: str) -> bool: ...

    @abstractmethod
    async def list_memories(
        self,
        namespace: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]: ...

    # ── Conversation memory (optional) ─────────────────────────────────

    async def create_event(
        self,
        actor_id: str,
        session_id: str,
        messages: list[tuple[str, str]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def list_events(
        self,
        actor_id: str,
        session_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def get_event(
        self,
        actor_id: str,
        session_id: str,
        event_id: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def delete_event(
        self,
        actor_id: str,
        session_id: str,
        event_id: str,
    ) -> None:
        raise NotImplementedError

    async def get_last_turns(
        self,
        actor_id: str,
        session_id: str,
        *,
        k: int = 5,
    ) -> list[list[dict[str, str]]]:
        raise NotImplementedError

    # ── Session management (optional) ────────────────────────────────

    async def list_actors(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def list_sessions(self, actor_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def delete_session(self, actor_id: str, session_id: str) -> None:
        raise NotImplementedError

    # ── Branch management (optional) ─────────────────────────────────

    async def fork_conversation(
        self,
        actor_id: str,
        session_id: str,
        root_event_id: str,
        branch_name: str,
        messages: list[tuple[str, str]],
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def list_branches(
        self,
        actor_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    # ── Control plane (optional) ─────────────────────────────────────

    async def create_memory_resource(
        self,
        name: str,
        *,
        strategies: list[dict[str, Any]] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def get_memory_resource(self, memory_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def list_memory_resources(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def delete_memory_resource(self, memory_id: str) -> None:
        raise NotImplementedError

    # ── Strategy management (optional) ───────────────────────────────

    async def list_strategies(self, memory_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def add_strategy(
        self,
        memory_id: str,
        strategy: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def delete_strategy(self, memory_id: str, strategy_id: str) -> None:
        raise NotImplementedError

    async def list_namespaces(self) -> list[str]:
        """List all known memory namespaces.

        Returns namespace identifiers that have stored memories.
        Providers that cannot enumerate namespaces should return an empty list.
        """
        return []

    async def healthcheck(self) -> bool | str:
        return True
