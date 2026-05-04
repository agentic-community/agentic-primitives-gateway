"""Tests for the agent-side knowledge tool handler.

The handler contract: ``knowledge_search`` reads its corpus namespace
from the ``knowledge_namespace`` contextvar — never from a kwarg, and
never from the memory namespace as a fallback.  These tests pin that
invariant so a future refactor can't quietly reintroduce cross-wiring.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.tools.handlers import knowledge_search
from agentic_primitives_gateway.primitives.knowledge.context import (
    reset_knowledge_namespace,
    set_knowledge_namespace,
)


class _Chunk:
    def __init__(self, text: str, score: float, source: str | None = None):
        self.text = text
        self.score = score
        self.metadata = {"source": source} if source else {}


@pytest.mark.asyncio
async def test_raises_without_contextvar():
    """No contextvar set → RuntimeError, not silent fallback to memory."""
    token = set_knowledge_namespace(None)
    try:
        with pytest.raises(RuntimeError, match="bound corpus namespace"):
            await knowledge_search(query="anything")
    finally:
        reset_knowledge_namespace(token)


@pytest.mark.asyncio
async def test_uses_contextvar_namespace():
    """Handler reads the contextvar and passes it to the registry provider.

    Also asserts the handler routes through ``retrieve()`` and NOT
    through ``query()``.  This is the canonical pattern advertised in
    ``CLAUDE.md``: "retrieve through knowledge, synthesize through the
    LLM primitive to keep credentials + audit + token accounting
    uniform."  If someone "optimises" this handler to call ``query()``
    so the backend does retrieve-and-generate in one step, the agent
    silently bypasses ``registry.llm`` for synthesis on backends that
    implement ``query()`` natively (AgentCore KB) — losing per-user
    credential isolation and LLM audit events for the synthesis half.
    """
    token = set_knowledge_namespace("support-kb")
    try:
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.knowledge.retrieve = AsyncMock(return_value=[_Chunk("the answer", 0.92, source="faq.md")])
            mock_reg.knowledge.query = AsyncMock()
            result = await knowledge_search(query="how do I reset my password", top_k=3)

        mock_reg.knowledge.retrieve.assert_awaited_once_with(
            namespace="support-kb",
            query="how do I reset my password",
            top_k=3,
            include_citations=False,
        )
        mock_reg.knowledge.query.assert_not_awaited()
        assert "the answer" in result
        assert "faq.md" in result
    finally:
        reset_knowledge_namespace(token)


@pytest.mark.asyncio
async def test_include_sources_attaches_structured_sideband():
    """include_sources=True attaches structured chunks for the UI without
    changing the LLM-facing output.

    The sideband is read from the ``current_artifact_structured``
    contextvar by the runner when it builds the ``ToolArtifact``.  The
    LLM only ever sees the compact bullet text — this protects the token
    cost invariant the user asked for.
    """
    from agentic_primitives_gateway.agents.tools.context import pop_current_artifact_structured
    from agentic_primitives_gateway.models.knowledge import Citation, RetrievedChunk

    token = set_knowledge_namespace("kb-a")
    try:
        chunk = RetrievedChunk(
            chunk_id="c1",
            document_id="d1",
            text="the answer",
            score=0.9,
            metadata={"source": "faq.md"},
            citations=[Citation(source="faq.md", page="3", snippet="the answer"[:200])],
        )
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.knowledge.retrieve = AsyncMock(return_value=[chunk])
            text = await knowledge_search(query="q", top_k=2, include_sources=True)

        mock_reg.knowledge.retrieve.assert_awaited_once_with(
            namespace="kb-a",
            query="q",
            top_k=2,
            include_citations=True,
        )
        # Plain-text output shape is unchanged by include_sources.
        assert "the answer" in text and "faq.md" in text

        # Structured sideband is set and carries the structured chunks.
        structured = pop_current_artifact_structured()
        assert structured is not None
        assert structured["kind"] == "knowledge_search"
        assert structured["namespace"] == "kb-a"
        assert len(structured["chunks"]) == 1
        payload_chunk = structured["chunks"][0]
        assert payload_chunk["text"] == "the answer"
        assert payload_chunk["citations"][0]["source"] == "faq.md"
        assert payload_chunk["citations"][0]["page"] == "3"
    finally:
        reset_knowledge_namespace(token)


@pytest.mark.asyncio
async def test_include_sources_default_does_not_set_sideband():
    """The default tool call must not leak a sideband into a subsequent call."""
    from agentic_primitives_gateway.agents.tools.context import pop_current_artifact_structured

    token = set_knowledge_namespace("kb")
    try:
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.knowledge.retrieve = AsyncMock(return_value=[_Chunk("x", 0.5)])
            await knowledge_search(query="q")
        assert pop_current_artifact_structured() is None
    finally:
        reset_knowledge_namespace(token)


@pytest.mark.asyncio
async def test_empty_result():
    token = set_knowledge_namespace("empty-corpus")
    try:
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.knowledge.retrieve = AsyncMock(return_value=[])
            result = await knowledge_search(query="whatever")
        assert "No relevant knowledge found" in result
    finally:
        reset_knowledge_namespace(token)


@pytest.mark.asyncio
async def test_inline_citations_prepends_numbered_markers_and_instructs_llm():
    """When the agent spec enables ``inline_citations``, the tool output
    given to the LLM must include ``[N]`` markers plus a one-line
    instruction telling the model to cite with them.  The UI depends
    on these markers for rendering pills; the instruction is what makes
    the model actually emit them.

    This pins a user-visible contract (what the model sees) rather than
    an implementation branch — rewrite the handler however you like as
    long as these two properties hold.
    """
    from agentic_primitives_gateway.agents.tools.context import pop_current_artifact_structured
    from agentic_primitives_gateway.models.knowledge import RetrievedChunk
    from agentic_primitives_gateway.primitives.knowledge.context import (
        reset_citation_counter,
        reset_knowledge_inline_citations,
        restore_citation_counter,
        set_knowledge_inline_citations,
    )

    ns_token = set_knowledge_namespace("kb")
    inline_token = set_knowledge_inline_citations(True)
    counter_token = reset_citation_counter()
    try:
        chunks = [
            RetrievedChunk(chunk_id="c1", document_id="d1", text="first", score=0.9, metadata={"source": "a.md"}),
            RetrievedChunk(chunk_id="c2", document_id="d2", text="second", score=0.8, metadata={"source": "b.md"}),
        ]
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.knowledge.retrieve = AsyncMock(return_value=chunks)
            text = await knowledge_search(query="q", top_k=2)

        # The inline-mode instructions + markers must both be in the text.
        assert "[0]" in text and "[1]" in text
        assert "[N]" in text  # instruction line mentions the marker format

        # The structured sideband carries matching citation_index values.
        structured = pop_current_artifact_structured()
        assert structured is not None
        assert structured["inline"] is True
        assert [c["citation_index"] for c in structured["chunks"]] == [0, 1]
    finally:
        restore_citation_counter(counter_token)
        reset_knowledge_inline_citations(inline_token)
        reset_knowledge_namespace(ns_token)


@pytest.mark.asyncio
async def test_inline_citations_across_multiple_calls_keeps_indices_unique():
    """Two knowledge_search calls in the same run must not collide on
    citation indices.  The second call's markers start where the first
    left off, so the UI can map ``[N]`` back to a specific chunk
    regardless of which call produced it.  This is the contract the
    per-run citation counter has to uphold.
    """
    from agentic_primitives_gateway.agents.tools.context import pop_current_artifact_structured
    from agentic_primitives_gateway.models.knowledge import RetrievedChunk
    from agentic_primitives_gateway.primitives.knowledge.context import (
        reset_citation_counter,
        reset_knowledge_inline_citations,
        restore_citation_counter,
        set_knowledge_inline_citations,
    )

    ns_token = set_knowledge_namespace("kb")
    inline_token = set_knowledge_inline_citations(True)
    counter_token = reset_citation_counter()
    try:
        first_chunks = [
            RetrievedChunk(chunk_id="c1", document_id="d1", text="one", score=0.9, metadata={}),
            RetrievedChunk(chunk_id="c2", document_id="d2", text="two", score=0.8, metadata={}),
        ]
        second_chunks = [
            RetrievedChunk(chunk_id="c3", document_id="d3", text="three", score=0.7, metadata={}),
        ]
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.knowledge.retrieve = AsyncMock(side_effect=[first_chunks, second_chunks])

            text_one = await knowledge_search(query="first query", top_k=2)
            structured_one = pop_current_artifact_structured()

            text_two = await knowledge_search(query="second query", top_k=1)
            structured_two = pop_current_artifact_structured()

        assert "[0]" in text_one and "[1]" in text_one
        # Second call must NOT reuse 0 / 1 — it picks up at 2.
        assert "[2]" in text_two
        assert "[0]" not in text_two and "[1]" not in text_two

        assert structured_one is not None and structured_two is not None
        assert [c["citation_index"] for c in structured_one["chunks"]] == [0, 1]
        assert [c["citation_index"] for c in structured_two["chunks"]] == [2]
    finally:
        restore_citation_counter(counter_token)
        reset_knowledge_inline_citations(inline_token)
        reset_knowledge_namespace(ns_token)


@pytest.mark.asyncio
async def test_inline_citations_off_emits_no_markers():
    """Regression guard: turning inline mode off must not leak ``[N]``
    markers into the LLM-facing text.  The default compact bullet
    format must be preserved bit-for-bit so agents without the opt-in
    don't see surprise changes in tool output.
    """
    from agentic_primitives_gateway.models.knowledge import RetrievedChunk
    from agentic_primitives_gateway.primitives.knowledge.context import (
        reset_knowledge_inline_citations,
        set_knowledge_inline_citations,
    )

    ns_token = set_knowledge_namespace("kb")
    inline_token = set_knowledge_inline_citations(False)
    try:
        chunks = [RetrievedChunk(chunk_id="c1", document_id="d1", text="hello", score=0.5, metadata={})]
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.knowledge.retrieve = AsyncMock(return_value=chunks)
            text = await knowledge_search(query="q")

        # No [N] markers; no instruction line; only the score prefix.
        assert "[0]" not in text
        assert "[N]" not in text
        assert "[0.50]" in text
    finally:
        reset_knowledge_inline_citations(inline_token)
        reset_knowledge_namespace(ns_token)


@pytest.mark.asyncio
async def test_include_sources_with_empty_result_still_attaches_sideband():
    """Contract: when the caller opted into sources, the UI should get a
    (possibly empty) structured payload so it can render "no sources"
    rather than falling back to plain-text rendering.  Empty chunk
    lists are still a meaningful signal.
    """
    from agentic_primitives_gateway.agents.tools.context import pop_current_artifact_structured

    token = set_knowledge_namespace("kb-empty")
    try:
        with patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_reg:
            mock_reg.knowledge.retrieve = AsyncMock(return_value=[])
            await knowledge_search(query="q", include_sources=True)
        structured = pop_current_artifact_structured()
        assert structured is not None
        assert structured["kind"] == "knowledge_search"
        assert structured["chunks"] == []
    finally:
        reset_knowledge_namespace(token)
