"""Intent test: ``LlamaIndexKnowledgeProvider.query`` synthesizes through
``registry.llm`` — not through a direct LlamaIndex LLM.

This is the single contract claim that makes the "retrieve through
knowledge, synthesize through the LLM primitive" pattern work:

  * Credentials: per-request OIDC / ``X-Cred-*`` resolution only happens
    inside the LLM primitive, not inside arbitrary LlamaIndex
    integrations.  If synthesis skips ``registry.llm``, we lose
    per-user credential isolation on the synthesis step.
  * Audit: the LLM ABC auto-emits ``llm.generate`` events + token
    metrics.  If synthesis skips the primitive, the compliance trail
    is missing for the synthesis half of RAG calls.
  * Provider routing: ``X-Provider-Llm`` is honored by the LLM
    primitive's routing layer, not by LlamaIndex — so a user who pins
    a specific LLM backend only gets it if we route through the gateway.

If someone "optimizes" ``query`` to call BedrockLLM / OpenAI directly,
every existing test for ``query`` still passes (MockEmbedding + any
installed LLM would return *something*) — only this test fails.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.primitives.knowledge.llamaindex import (
    LlamaIndexKnowledgeProvider,
)


@pytest.fixture
def mock_embed_settings() -> Generator[None]:
    from llama_index.core import Settings
    from llama_index.core.embeddings.mock_embed_model import MockEmbedding

    Settings.embed_model = MockEmbedding(embed_dim=8)
    yield


@pytest.fixture
def provider(mock_embed_settings: None) -> LlamaIndexKnowledgeProvider:
    p = LlamaIndexKnowledgeProvider(store_type="vector")
    return p


class TestQueryRoutesThroughLLMPrimitive:
    async def test_query_invokes_registry_llm_route_request(self, provider: LlamaIndexKnowledgeProvider) -> None:
        from agentic_primitives_gateway.models.knowledge import IngestDocument

        await provider.ingest(
            "ns-1",
            [IngestDocument(text="The capital of France is Paris.")],
        )

        # Patch the registry the bridge *reads at call time* — any
        # production refactor that bypasses the registry breaks this.
        mock_llm = AsyncMock(
            return_value={
                "content": "Paris",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 1},
            }
        )

        with patch("agentic_primitives_gateway.registry.registry") as mock_registry:
            mock_registry.llm.route_request = mock_llm
            await provider.query("ns-1", "What is the capital of France?", top_k=1)

        assert mock_llm.await_count >= 1, (
            "query() must route synthesis through registry.llm.route_request — "
            "if this fails, synthesis is bypassing the LLM primitive and "
            "losing per-user credential resolution + LLM audit events."
        )

        # Sanity-check: the request carried a messages payload (so the
        # bridge built a real gateway LLM request, not a trivial no-op).
        call_args = mock_llm.await_args_list[0]
        sent_request = call_args.args[0] if call_args.args else call_args.kwargs.get("model_request")
        assert isinstance(sent_request, dict)
        assert "messages" in sent_request
        assert sent_request["messages"], "registry.llm call must include prompt messages"

    async def test_query_propagates_backend_name_as_provider_override(
        self, provider: LlamaIndexKnowledgeProvider, mock_embed_settings: None
    ) -> None:
        """When ``llm.backend_name`` is configured, the bridge must pin
        that backend via ``_provider_override`` — the same pin users get
        from the ``X-Provider-Llm`` header.
        """
        from agentic_primitives_gateway.models.knowledge import IngestDocument

        pinned = LlamaIndexKnowledgeProvider(
            store_type="vector",
            llm={"backend_name": "my-pinned-backend", "max_tokens": 64},
        )
        await pinned.ingest("ns", [IngestDocument(text="hello")])

        captured: list[dict[str, Any]] = []

        async def _capture(req: dict[str, Any]) -> dict[str, Any]:
            captured.append(req)
            return {"content": "ok", "usage": {}}

        with patch("agentic_primitives_gateway.registry.registry") as mock_registry:
            mock_registry.llm.route_request = _capture
            await pinned.query("ns", "q", top_k=1)

        assert captured, "registry.llm.route_request was not called"
        assert captured[0].get("_provider_override") == "my-pinned-backend"
