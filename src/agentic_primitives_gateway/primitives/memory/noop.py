from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult
from agentic_primitives_gateway.primitives.memory.base import MemoryProvider

logger = logging.getLogger(__name__)


class NoopMemoryProvider(MemoryProvider):
    """No-op memory provider that logs calls but stores nothing."""

    def __init__(self, **kwargs: Any) -> None:
        logger.info("NoopMemoryProvider initialized")

    async def store(
        self,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        logger.debug("noop store: ns=%s key=%s", namespace, key)
        return MemoryRecord(
            namespace=namespace,
            key=key,
            content=content,
            metadata=metadata or {},
        )

    async def retrieve(self, namespace: str, key: str) -> MemoryRecord | None:
        logger.debug("noop retrieve: ns=%s key=%s", namespace, key)
        return None

    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        logger.debug("noop search: ns=%s query=%s", namespace, query)
        return []

    async def delete(self, namespace: str, key: str) -> bool:
        logger.debug("noop delete: ns=%s key=%s", namespace, key)
        return False

    async def list_memories(
        self,
        namespace: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        logger.debug("noop list_memories: ns=%s", namespace)
        return []
