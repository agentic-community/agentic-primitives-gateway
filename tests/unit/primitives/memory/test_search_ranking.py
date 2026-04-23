"""Intent-level test: memory search returns results ranked by score descending.

Contract (stated implicitly by ``MemoryProvider.search`` returning
``list[SearchResult]`` with a ``score`` field): results come back in
score-descending order so the caller can trust ``results[0]`` is the
best match.  The ``MetricsProxy`` wraps every provider, so any
provider's ``search()`` output feeds directly into agent tools,
client callers, and the LLM — the order matters for the model's
decision-making.

Existing tests (``test_in_memory.py``):
- ``test_search_finds_match`` stores two records with different content
  and asserts the *count* is 1.  Doesn't exercise ranking.
- ``test_search_respects_top_k`` stores five records of *identical*
  length (``"item number 0"`` … ``"item number 4"``) and asserts the
  top-k *count* is 2.  Every record's score is identical, so a
  broken sort order goes undetected.

This file fills the gap: store records with deliberately different
relevance scores (driven by content length in the in-memory
provider's formula) and assert the returned order matches the
expected ranking.
"""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.primitives.memory.in_memory import InMemoryProvider


class TestInMemorySearchRanking:
    """The in-memory provider's scoring formula is
    ``len(query) / len(content)`` — tighter matches rank higher.
    The specific formula isn't the contract; "results are ordered by
    score descending" is.  If the sort breaks, these tests fail.
    """

    @pytest.mark.asyncio
    async def test_shorter_content_ranks_higher(self):
        """Three records containing ``python`` with different lengths.
        The shortest (highest query-coverage ratio) must come first.
        """
        p = InMemoryProvider()
        # Varied lengths all containing "python".
        await p.store("ns", "short", "python")
        await p.store("ns", "medium", "python is a language")
        await p.store("ns", "long", "python is a general-purpose programming language used widely")

        results = await p.search("ns", "python", top_k=10)

        assert len(results) == 3
        keys_in_order = [r.record.key for r in results]
        assert keys_in_order == ["short", "medium", "long"], (
            f"Expected ranking short→medium→long by score descending, got {keys_in_order}. "
            f"Scores: {[(r.record.key, r.score) for r in results]}"
        )
        # Strictly monotonic — not just ties.
        scores = [r.score for r in results]
        assert scores[0] > scores[1] > scores[2]

    @pytest.mark.asyncio
    async def test_top_k_returns_highest_scoring_not_arbitrary(self):
        """Store five records with predictable scores; ``top_k=2``
        must return the two *highest-scoring* ones, not just any two.
        A broken sort that happened to return the last-inserted two
        would pass the existing ``test_search_respects_top_k`` because
        that test only checks the count.
        """
        p = InMemoryProvider()
        # Insertion order deliberately misaligned with score order so a
        # test that returned the first/last two inserted would fail.
        await p.store("ns", "longest", "apple banana cherry date elderberry fig apple")  # low score
        await p.store("ns", "shortest", "apple")  # highest score
        await p.store("ns", "medium-1", "apple banana cherry")
        await p.store("ns", "medium-2", "apple banana cherry date")
        await p.store("ns", "long", "apple banana cherry date elderberry")

        results = await p.search("ns", "apple", top_k=2)

        assert len(results) == 2
        keys = [r.record.key for r in results]
        assert keys == ["shortest", "medium-1"], (
            f"Expected top-2 by score (shortest, medium-1), got {keys}. "
            f"If this returned insertion-order top-2, the sort is broken."
        )

    @pytest.mark.asyncio
    async def test_search_order_stable_after_new_inserts(self):
        """A new insert with a mid-range score doesn't reshuffle the
        higher-scoring records.  Guards against a regression where
        sorting was accidentally by ``created_at`` instead of score.
        """
        p = InMemoryProvider()
        await p.store("ns", "short", "dog")
        await p.store("ns", "medium", "dog runs fast")
        await p.store("ns", "long", "dog runs very fast across the field")

        # Insert a new mid-length record AFTER the others.
        await p.store("ns", "new-medium", "dog barks loud")  # similar length to medium

        results = await p.search("ns", "dog", top_k=10)
        # "short" (len=3) must still rank first regardless of insert order.
        assert results[0].record.key == "short", (
            f"Highest-scoring record 'short' should be #1 regardless of insert order; "
            f"got {[r.record.key for r in results]}.  "
            "If sorting uses insertion time, this fails."
        )

    @pytest.mark.asyncio
    async def test_results_sorted_strictly_descending_by_score(self):
        """General invariant: every adjacent pair in the result list
        satisfies ``score[i] >= score[i+1]``.  Catches any sort-order
        regression without coupling to specific ranking strategy.
        """
        p = InMemoryProvider()
        # A mix of lengths and query-containing content.
        contents = [
            "match",
            "match and more",
            "this has match in the middle",
            "match match match",
            "match match",
        ]
        for i, c in enumerate(contents):
            await p.store("ns", f"k{i}", c)

        results = await p.search("ns", "match", top_k=10)
        assert len(results) == 5
        scores = [r.score for r in results]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Result list not sorted by score descending at position {i}: "
                f"{scores[i]} < {scores[i + 1]}.  Full scores: {scores}"
            )
