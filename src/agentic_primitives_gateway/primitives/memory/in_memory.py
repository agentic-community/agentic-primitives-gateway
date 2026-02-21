from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult
from agentic_primitives_gateway.primitives.memory.base import MemoryProvider


class InMemoryProvider(MemoryProvider):
    """Simple dict-based memory provider for development and testing.

    Uses substring matching for search. Not suitable for production use.
    """

    def __init__(self, **kwargs: Any) -> None:
        # namespace -> key -> MemoryRecord
        self._store: dict[str, dict[str, MemoryRecord]] = {}

    async def store(
        self,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        now = datetime.now(UTC)
        ns_store = self._store.setdefault(namespace, {})

        existing = ns_store.get(key)
        record = MemoryRecord(
            namespace=namespace,
            key=key,
            content=content,
            metadata=metadata or {},
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        ns_store[key] = record
        return record

    async def retrieve(self, namespace: str, key: str) -> MemoryRecord | None:
        return self._store.get(namespace, {}).get(key)

    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        ns_store = self._store.get(namespace, {})
        results: list[SearchResult] = []
        query_lower = query.lower()

        for record in ns_store.values():
            if filters and not self._matches_filters(record, filters):
                continue

            content_lower = record.content.lower()
            if query_lower in content_lower:
                # Simple relevance score based on query coverage
                score = len(query_lower) / max(len(content_lower), 1)
                results.append(SearchResult(record=record, score=score))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    async def delete(self, namespace: str, key: str) -> bool:
        ns_store = self._store.get(namespace, {})
        if key in ns_store:
            del ns_store[key]
            return True
        return False

    async def list_memories(
        self,
        namespace: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        ns_store = self._store.get(namespace, {})
        records = list(ns_store.values())

        if filters:
            records = [r for r in records if self._matches_filters(r, filters)]

        records.sort(key=lambda r: r.created_at, reverse=True)
        return records[offset : offset + limit]

    @staticmethod
    def _matches_filters(record: MemoryRecord, filters: dict[str, Any]) -> bool:
        return all(record.metadata.get(fk) == fv for fk, fv in filters.items())
