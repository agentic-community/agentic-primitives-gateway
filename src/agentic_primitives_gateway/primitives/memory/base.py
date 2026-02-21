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

    async def healthcheck(self) -> bool:
        return True
