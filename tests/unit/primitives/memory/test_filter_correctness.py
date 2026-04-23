"""Intent-level test: memory search + list filters actually restrict results.

Contract: ``search(filters={k: v})`` and ``list_memories(filters={k: v})``
return **only** records whose metadata matches every filter key-value.
The filter system is the way callers narrow results when a namespace
has too much data to sift through — if it silently returns
unmatched records, callers get wrong answers with no signal.

Existing tests use the provider's API at the route level (via
``TestClient``) and mostly exercise count-only invariants.  No
existing test stores records with distinct metadata, applies a
filter, and asserts only the matching records come back.

The failure mode this catches: a regression where
``_matches_filters`` silently short-circuits, or where a filter key
with an unusual value type (int, bool, list) slips through.
"""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.primitives.memory.in_memory import InMemoryProvider


class TestSearchFilter:
    @pytest.mark.asyncio
    async def test_search_with_tag_filter_excludes_unmatched(self):
        p = InMemoryProvider()
        await p.store("ns", "a", "apple red fruit", metadata={"tag": "fruit"})
        await p.store("ns", "b", "apple laptop brand", metadata={"tag": "tech"})
        await p.store("ns", "c", "apple pie recipe", metadata={"tag": "fruit"})

        results = await p.search("ns", "apple", top_k=10, filters={"tag": "fruit"})

        assert {r.record.key for r in results} == {"a", "c"}, (
            f"Filter tag=fruit should return only fruit records; got "
            f"{[(r.record.key, r.record.metadata) for r in results]}"
        )

    @pytest.mark.asyncio
    async def test_search_with_multiple_filters_requires_all_match(self):
        """Multiple filter keys → record must match ALL of them (AND)."""
        p = InMemoryProvider()
        await p.store("ns", "a", "doc one", metadata={"tag": "x", "user": "alice"})
        await p.store("ns", "b", "doc two", metadata={"tag": "x", "user": "bob"})
        await p.store("ns", "c", "doc three", metadata={"tag": "y", "user": "alice"})

        results = await p.search("ns", "doc", top_k=10, filters={"tag": "x", "user": "alice"})

        assert len(results) == 1
        assert results[0].record.key == "a", (
            f"Expected only record 'a' (matches both tag=x AND user=alice); got "
            f"{[(r.record.key, r.record.metadata) for r in results]}"
        )

    @pytest.mark.asyncio
    async def test_search_filter_with_nonexistent_key_returns_empty(self):
        """A filter on a metadata key no record has → zero results.
        (A regression that treated missing keys as wildcards would
        incorrectly return all records.)
        """
        p = InMemoryProvider()
        await p.store("ns", "a", "apple", metadata={"tag": "fruit"})
        await p.store("ns", "b", "banana", metadata={"tag": "fruit"})

        results = await p.search("ns", "a", top_k=10, filters={"nonexistent_key": "anything"})
        assert results == [], f"Expected zero results, got {[r.record.key for r in results]}"

    @pytest.mark.asyncio
    async def test_search_without_filters_returns_all_matches(self):
        """Sanity: ``filters=None`` means "no filter", not "no records"."""
        p = InMemoryProvider()
        await p.store("ns", "a", "match", metadata={"tag": "x"})
        await p.store("ns", "b", "match me", metadata={"tag": "y"})

        results = await p.search("ns", "match", top_k=10, filters=None)
        assert {r.record.key for r in results} == {"a", "b"}


class TestListMemoriesFilter:
    """``list_memories`` uses the same ``_matches_filters`` helper;
    verify the same invariants via the list API.
    """

    @pytest.mark.asyncio
    async def test_list_with_filter_excludes_unmatched(self):
        p = InMemoryProvider()
        await p.store("ns", "a", "x", metadata={"kind": "note"})
        await p.store("ns", "b", "y", metadata={"kind": "todo"})
        await p.store("ns", "c", "z", metadata={"kind": "note"})

        result = await p.list_memories("ns", filters={"kind": "note"})
        assert {r.key for r in result} == {"a", "c"}

    @pytest.mark.asyncio
    async def test_list_with_multiple_filters(self):
        p = InMemoryProvider()
        await p.store("ns", "a", "x", metadata={"kind": "note", "author": "alice"})
        await p.store("ns", "b", "y", metadata={"kind": "note", "author": "bob"})

        result = await p.list_memories("ns", filters={"kind": "note", "author": "alice"})
        assert [r.key for r in result] == ["a"]


class TestFilterValueTypes:
    """Filters should match on exact value equality across types
    (str, int, bool) — ``_matches_filters`` uses ``==`` so this is
    delivered, but a regression that str-cast values or mishandled
    falsy types would break it.
    """

    @pytest.mark.asyncio
    async def test_filter_matches_integer_metadata(self):
        p = InMemoryProvider()
        await p.store("ns", "a", "x", metadata={"priority": 1})
        await p.store("ns", "b", "y", metadata={"priority": 2})

        results = await p.search("ns", "x", top_k=10, filters={"priority": 1})
        assert [r.record.key for r in results] == ["a"]

    @pytest.mark.asyncio
    async def test_filter_matches_boolean_metadata(self):
        p = InMemoryProvider()
        await p.store("ns", "a", "x", metadata={"pinned": True})
        await p.store("ns", "b", "y", metadata={"pinned": False})

        results = await p.search("ns", "x", top_k=10, filters={"pinned": True})
        assert [r.record.key for r in results] == ["a"]

    @pytest.mark.asyncio
    async def test_filter_does_not_str_coerce(self):
        """``filter={priority: 1}`` must not match a record with
        ``metadata={priority: "1"}`` — ``1 == "1"`` is False in
        Python, but a regression that JSON-round-tripped or string-
        coerced would silently match.
        """
        p = InMemoryProvider()
        await p.store("ns", "a", "x", metadata={"priority": 1})
        await p.store("ns", "b", "y", metadata={"priority": "1"})

        results = await p.search("ns", "x", top_k=10, filters={"priority": 1})
        assert [r.record.key for r in results] == ["a"]
