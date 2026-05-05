"""Intent test: ``LlamaIndexKnowledgeProvider.query`` synthesizes through
the gateway's LLM primitive — not through a direct LlamaIndex LLM.

This is the single contract claim that makes the "retrieve through
knowledge, synthesize through the LLM primitive" pattern work:

  * Credentials: per-request OIDC / ``X-Cred-*`` resolution only happens
    inside the LLM primitive, not inside arbitrary LlamaIndex
    integrations.  If synthesis skips the primitive, we lose per-user
    credential isolation on the synthesis step.
  * Audit: the LLM ABC auto-emits ``llm.generate`` events + token
    metrics.  If synthesis skips the primitive, the compliance trail
    is missing for the synthesis half of RAG calls.
  * Operator-scope backend selection: ``llm.backend_name`` on the
    knowledge config (or ``providers.llm.default`` as fallback) picks
    the synthesis LLM.  The caller's ``X-Provider-Llm`` header must
    NOT influence synthesis — that header is for caller-facing LLM
    calls (chat completions), not for the knowledge provider's
    internal synthesis model choice.

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
    async def test_query_invokes_llm_primitive_route_request(self, provider: LlamaIndexKnowledgeProvider) -> None:
        """The bridge must route synthesis through a provider resolved
        from the LLM primitive registry — not a LlamaIndex-native LLM
        client, not a raw SDK call.  We patch ``registry.get_primitive``
        because that's how the bridge resolves the provider today
        (explicitly, bypassing the ``_provider_overrides`` contextvar).
        """
        from agentic_primitives_gateway.models.knowledge import IngestDocument

        await provider.ingest(
            "ns-1",
            [IngestDocument(text="The capital of France is Paris.")],
        )

        captured: list[dict[str, Any]] = []

        async def _capture(req: dict[str, Any]) -> dict[str, Any]:
            captured.append(req)
            return {
                "content": "Paris",
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 4, "output_tokens": 1},
            }

        synth_provider = AsyncMock()
        synth_provider.route_request = _capture

        with patch("agentic_primitives_gateway.registry.registry") as mock_registry:
            mock_primitive = mock_registry.get_primitive.return_value
            mock_primitive.default_name = "default"
            mock_primitive.get.return_value = synth_provider
            await provider.query("ns-1", "What is the capital of France?", top_k=1)

        assert captured, (
            "query() must route synthesis through the LLM primitive — "
            "if this fails, synthesis is bypassing the primitive and "
            "losing per-user credential resolution + LLM audit events."
        )
        # Sanity-check: the request carried a messages payload (so the
        # bridge built a real gateway LLM request, not a trivial no-op).
        assert isinstance(captured[0], dict)
        assert "messages" in captured[0]
        assert captured[0]["messages"], "LLM primitive call must include prompt messages"

    async def test_query_resolves_pinned_backend_by_name(
        self, provider: LlamaIndexKnowledgeProvider, mock_embed_settings: None
    ) -> None:
        """When ``llm.backend_name`` is configured, the bridge must
        resolve that named backend explicitly from the registry — NOT
        stuff the name into the request payload (where nothing reads
        it) and fall through to ``registry.llm`` (which resolves via
        the ``_provider_overrides`` contextvar from ``X-Provider-Llm``).

        The difference matters: the contextvar path is caller-driven
        (whatever the HTTP request asked for), whereas
        ``llm.backend_name`` is a *knowledge-backend-operator* decision
        that should pin the synthesis model regardless of the caller's
        header.  An earlier version of this bridge silently dropped the
        pin — the operator's config did nothing.

        This test pins the real behavior: with ``backend_name`` set,
        ``registry.get_primitive(LLM).get(name=backend_name)`` is the
        entry point; ``registry.llm`` is not invoked.
        """
        from agentic_primitives_gateway.models.enums import Primitive
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

        pinned_provider = AsyncMock()
        pinned_provider.route_request = _capture

        # registry.get_primitive(LLM).get(name=...) is the resolution
        # path under test.  registry.llm is the fallback path that
        # would be taken if the bug regressed — the AssertionError
        # side-effect below fires if that path is ever hit.
        with patch("agentic_primitives_gateway.registry.registry") as mock_registry:
            mock_primitive = mock_registry.get_primitive.return_value
            mock_primitive.default_name = "default"
            mock_primitive.get.return_value = pinned_provider
            mock_registry.llm.route_request.side_effect = AssertionError(
                "pinned backend bypass regressed — route_request hit the default provider"
            )
            await pinned.query("ns", "q", top_k=1)

        # The registry resolver was called with the pinned name.
        mock_registry.get_primitive.assert_called_with(Primitive.LLM)
        mock_primitive.get.assert_called_with(name="my-pinned-backend")

        # The request that actually went through is clean — no more
        # dead ``_provider_override`` key polluting the payload.
        assert captured, "pinned provider's route_request was not called"
        assert "_provider_override" not in captured[0]

    async def test_unset_backend_name_uses_llm_primitive_default_not_contextvar(
        self, provider: LlamaIndexKnowledgeProvider, mock_embed_settings: None
    ) -> None:
        """When ``llm.backend_name`` is unset, the bridge must fall
        back to the LLM primitive's operator-declared ``default_name``
        — NOT to ``registry.llm``, which would consult the
        ``_provider_overrides`` contextvar populated from
        ``X-Provider-Llm`` headers.

        Regression scenario: a caller chatting with an agent sends
        ``X-Provider-Llm: openai`` to route their chat to OpenAI.

        Contract: synthesis with ``backend_name`` unset uses
        ``providers.llm.default``, bypassing the contextvar.  Matches
        LlamaIndex's own ``llm or Settings.llm`` idiom where
        ``Settings.llm`` is the operator-declared default, not
        a caller-routable choice.
        """
        from agentic_primitives_gateway.models.enums import Primitive
        from agentic_primitives_gateway.models.knowledge import IngestDocument

        await provider.ingest("ns", [IngestDocument(text="hello")])

        captured: list[dict[str, Any]] = []

        async def _capture(req: dict[str, Any]) -> dict[str, Any]:
            captured.append(req)
            return {"content": "ok", "usage": {}}

        default_provider = AsyncMock()
        default_provider.route_request = _capture

        with patch("agentic_primitives_gateway.registry.registry") as mock_registry:
            mock_primitive = mock_registry.get_primitive.return_value
            mock_primitive.default_name = "bedrock"
            mock_primitive.get.return_value = default_provider
            # The contextvar-driven path (``registry.llm``) must NOT
            # be hit for synthesis — if it is, the caller could have
            # redirected the synthesis model via X-Provider-Llm.
            mock_registry.llm.route_request.side_effect = AssertionError(
                "unset backend_name must bypass the contextvar — registry.llm was hit, which reads X-Provider-Llm"
            )
            await provider.query("ns", "q", top_k=1)

        # Resolution went through the explicit-name API with the
        # LLM primitive's operator-declared default.
        mock_registry.get_primitive.assert_called_with(Primitive.LLM)
        mock_primitive.get.assert_called_with(name="bedrock")
        assert captured, "default provider's route_request was not called"
