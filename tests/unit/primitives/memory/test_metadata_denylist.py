"""Memory primitive metadata_denylist — read-path scrubbing.

Contract: ``memory.metadata_denylist`` keys are stripped from every
``MemoryRecord.metadata`` that leaves the provider, regardless of
which read method the caller used.  Write paths (``store``) are NOT
scrubbed — the denylist filters on the way out, not on the way in.

These tests exercise the wrapper installed by ``MemoryProvider.__init_subclass__``.
Using a throwaway subclass is deliberate: it pins the ABC-level
contract rather than any specific backend, so the test survives
provider refactors and catches regressions in the inherited wrapper.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult
from agentic_primitives_gateway.primitives.memory.base import MemoryProvider


class _NoisyMemoryProvider(MemoryProvider):
    """Minimal backend that returns records with denylisted metadata.

    Each read method returns the same kind of leaky record so a single
    denylist setting should cover retrieve / search / list_memories in
    one go — proving the ABC wrapper covers every read path.
    """

    async def store(
        self,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        return MemoryRecord(namespace=namespace, key=key, content=content, metadata=dict(metadata or {}))

    async def retrieve(self, namespace: str, key: str) -> MemoryRecord | None:
        return MemoryRecord(
            namespace=namespace,
            key=key,
            content="x",
            metadata={"source": "user", "internal_id": "leak", "pipeline_stage": "raw"},
        )

    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        rec = MemoryRecord(
            namespace=namespace,
            key="k",
            content="x",
            metadata={"source": "user", "internal_id": "leak"},
        )
        return [SearchResult(record=rec, score=0.9)]

    async def delete(self, namespace: str, key: str) -> bool:
        return True

    async def list_memories(
        self,
        namespace: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        return [
            MemoryRecord(
                namespace=namespace,
                key=f"k{i}",
                content="x",
                metadata={"source": "user", "internal_id": f"leak-{i}"},
            )
            for i in range(3)
        ]


@pytest.fixture
def denylist_patch():
    """Patch the shared lookup so tests control the denylist per case
    without mutating global settings.  Using the shared name proves
    memory goes through the same chokepoint as knowledge.
    """
    with patch("agentic_primitives_gateway.primitives.memory._audit.get_denylist") as m:
        m.return_value = []
        yield m


class TestMemoryDenylistReadPath:
    async def test_empty_denylist_is_noop(self, denylist_patch) -> None:
        """Default config keeps all fields.  This is the shape the
        field observes without opt-in."""
        denylist_patch.return_value = []
        provider = _NoisyMemoryProvider()
        record = await provider.retrieve("ns", "k")
        assert record is not None
        assert record.metadata["internal_id"] == "leak"

    async def test_retrieve_strips_denylisted_keys(self, denylist_patch) -> None:
        denylist_patch.return_value = ["internal_id"]
        provider = _NoisyMemoryProvider()
        record = await provider.retrieve("ns", "k")
        assert record is not None
        assert "internal_id" not in record.metadata
        # Legit keys survive — denylist not allowlist.
        assert record.metadata["source"] == "user"
        assert record.metadata["pipeline_stage"] == "raw"

    async def test_search_strips_from_nested_record_metadata(self, denylist_patch) -> None:
        """``SearchResult.record.metadata`` is the nesting that makes
        the extractor non-trivial — pin that the wrapper reaches in.
        """
        denylist_patch.return_value = ["internal_id"]
        provider = _NoisyMemoryProvider()
        results = await provider.search("ns", "q")
        assert len(results) == 1
        assert "internal_id" not in results[0].record.metadata

    async def test_list_memories_strips_from_every_record(self, denylist_patch) -> None:
        denylist_patch.return_value = ["internal_id"]
        provider = _NoisyMemoryProvider()
        records = await provider.list_memories("ns")
        assert len(records) == 3
        for r in records:
            assert "internal_id" not in r.metadata
            assert r.metadata["source"] == "user"

    async def test_store_is_not_scrubbed(self, denylist_patch) -> None:
        """Contract: scrubbing only runs on the read path.  If the
        operator wants a field stored and then hidden on retrieval,
        the denylist must preserve it at write time.  Scrubbing on
        write would delete the operator's own data — the opposite of
        the feature.
        """
        denylist_patch.return_value = ["internal_id"]
        provider = _NoisyMemoryProvider()
        record = await provider.store("ns", "k", "content", metadata={"internal_id": "must-persist-at-rest"})
        assert record.metadata["internal_id"] == "must-persist-at-rest"
