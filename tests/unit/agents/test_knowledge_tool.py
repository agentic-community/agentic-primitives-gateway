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
        )
        mock_reg.knowledge.query.assert_not_awaited()
        assert "the answer" in result
        assert "faq.md" in result
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
