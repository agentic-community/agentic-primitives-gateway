from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult


class MemoryProvider(ABC):
    """Abstract base class for memory providers.

    Memory providers handle storage, retrieval, and search of agent memories.
    Implementations may use frameworks like mem0 or langmem on top of vector
    stores like Milvus or Weaviate.
    """

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

    async def healthcheck(self) -> bool:
        return True
