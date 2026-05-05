"""Tests for the Citation contract + ``include_citations`` flag + metadata_denylist.

These guard the design the user landed on after two rounds of review:

- ``retrieve()`` gains an optional ``include_citations`` flag on the ABC
  so REST callers and agent tools both benefit.
- Providers that can produce structured citations populate them;
  providers that can't leave ``RetrievedChunk.citations = None``.
- A config-level ``knowledge.metadata_denylist`` is applied uniformly in
  ``_audit.wrap_retrieve`` so REST and agent paths see the same scrubbed
  metadata — no per-path duplication.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.models.knowledge import (
    Citation,
    DocumentInfo,
    IngestDocument,
    IngestResult,
    RetrievedChunk,
)
from agentic_primitives_gateway.primitives.knowledge.base import KnowledgeProvider

# ── Citation model ────────────────────────────────────────────────────


class TestCitationModel:
    def test_defaults_all_fields_optional(self) -> None:
        c = Citation()
        assert c.source is None and c.uri is None and c.page is None
        assert c.span is None and c.snippet is None
        assert c.metadata == {}

    def test_span_is_tuple(self) -> None:
        c = Citation(span=(0, 120))
        assert c.span == (0, 120)

    def test_metadata_is_free_form_escape_hatch(self) -> None:
        c = Citation(metadata={"element_id": "p-42", "confidence": 0.9})
        assert c.metadata["element_id"] == "p-42"


class TestRetrievedChunkCitations:
    def test_citations_default_none(self) -> None:
        """Important: default is ``None``, not ``[]``.  ``None`` means
        "the provider didn't populate citations" — distinct from "the
        provider populated citations but got back zero references."
        """
        chunk = RetrievedChunk(chunk_id="c1", document_id="d1", text="x")
        assert chunk.citations is None

    def test_citations_list_shape(self) -> None:
        chunk = RetrievedChunk(
            chunk_id="c1",
            document_id="d1",
            text="x",
            citations=[Citation(source="a.pdf", page="3")],
        )
        assert chunk.citations is not None
        assert chunk.citations[0].source == "a.pdf"


# ── include_citations flag plumbing ────────────────────────────────────


class _TracingProvider(KnowledgeProvider):
    """Records the ``include_citations`` value passed to ``retrieve``.

    Using a per-test subclass is deliberate: it exercises the real ABC
    wrapper (``__init_subclass__`` auto-audits retrieval), which is the
    layer where the flag's plumbing has to survive.  Mocking the wrapper
    would hide regressions in exactly the code we care about.
    """

    store_type = "probe"

    def __init__(self) -> None:
        self.last_include_citations: bool | None = None

    async def ingest(self, namespace: str, documents: list[IngestDocument]) -> IngestResult:
        return IngestResult(document_ids=[], ingested=0)

    async def retrieve(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        *,
        include_citations: bool = False,
    ) -> list[RetrievedChunk]:
        self.last_include_citations = include_citations
        citations = [Citation(source="x.md", page="1")] if include_citations else None
        return [
            RetrievedChunk(
                chunk_id="c1",
                document_id="d1",
                text="hit",
                score=0.8,
                metadata={"source": "x.md"},
                citations=citations,
            )
        ]

    async def delete(self, namespace: str, document_id: str) -> bool:
        return False

    async def list_documents(self, namespace: str, limit: int = 100, offset: int = 0) -> list[DocumentInfo]:
        return []


class TestIncludeCitationsFlag:
    async def test_flag_default_false_leaves_citations_none(self) -> None:
        provider = _TracingProvider()
        chunks = await provider.retrieve("ns", "q")
        assert provider.last_include_citations is False
        assert chunks[0].citations is None

    async def test_flag_true_propagates_to_provider(self) -> None:
        provider = _TracingProvider()
        chunks = await provider.retrieve("ns", "q", include_citations=True)
        assert provider.last_include_citations is True
        assert chunks[0].citations is not None
        assert chunks[0].citations[0].source == "x.md"


# ── metadata_denylist applied uniformly ───────────────────────────────


@pytest.fixture
def denylist_patch() -> Generator[MagicMock]:
    """Patch the shared denylist lookup so tests don't need to mutate
    global settings.  The wrapper reads the list lazily at call time,
    so this gives per-test control.  We patch the imported name inside
    ``knowledge._audit`` rather than at the source so the mock only
    affects knowledge lookups — if other primitives pull the same
    helper concurrently in the future, their tests won't collide.
    """
    with patch("agentic_primitives_gateway.primitives.knowledge._audit.get_denylist") as m:
        m.return_value = []
        yield m


class _NoisyProvider(KnowledgeProvider):
    """Returns chunks whose metadata contains denylisted keys.

    This provider is the "leaky" shape the denylist has to scrub: an
    operator (or ingest pipeline) attached fields that shouldn't reach
    clients.  The ABC wrapper is the single chokepoint that strips them.
    """

    store_type = "probe"

    async def ingest(self, namespace: str, documents: list[IngestDocument]) -> IngestResult:
        return IngestResult(document_ids=[], ingested=0)

    async def retrieve(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        *,
        include_citations: bool = False,
    ) -> list[RetrievedChunk]:
        citations = [Citation(metadata={"internal_id": "secret", "page": "2"})] if include_citations else None
        return [
            RetrievedChunk(
                chunk_id="c1",
                document_id="d1",
                text="text",
                score=0.5,
                metadata={"source": "a.md", "internal_id": "secret", "author": "alice"},
                citations=citations,
            )
        ]

    async def delete(self, namespace: str, document_id: str) -> bool:
        return False

    async def list_documents(self, namespace: str, limit: int = 100, offset: int = 0) -> list[DocumentInfo]:
        return []


class TestMetadataDenylist:
    async def test_empty_denylist_is_a_noop(self, denylist_patch: MagicMock) -> None:
        denylist_patch.return_value = []
        provider = _NoisyProvider()
        chunks = await provider.retrieve("ns", "q")
        assert chunks[0].metadata["internal_id"] == "secret"
        assert chunks[0].metadata["author"] == "alice"

    async def test_denylist_strips_top_level_keys_from_chunk_metadata(self, denylist_patch: MagicMock) -> None:
        denylist_patch.return_value = ["internal_id"]
        provider = _NoisyProvider()
        chunks = await provider.retrieve("ns", "q")
        assert "internal_id" not in chunks[0].metadata
        # Legit keys survive — that's the point: denylist not allowlist.
        assert chunks[0].metadata["source"] == "a.md"
        assert chunks[0].metadata["author"] == "alice"

    async def test_denylist_strips_citation_metadata(self, denylist_patch: MagicMock) -> None:
        """Citations carry their own metadata dict — the scrubber must
        reach into them, otherwise operators could still leak via the
        citation.metadata passthrough."""
        denylist_patch.return_value = ["internal_id"]
        provider = _NoisyProvider()
        chunks = await provider.retrieve("ns", "q", include_citations=True)
        assert chunks[0].citations is not None
        assert "internal_id" not in chunks[0].citations[0].metadata
        assert chunks[0].citations[0].metadata["page"] == "2"

    async def test_denylist_applies_regardless_of_include_citations(self, denylist_patch: MagicMock) -> None:
        """The denylist has to run on every retrieve call, not just when
        citations were requested.  Otherwise operators would have to
        remember to turn the flag on to get the scrubbing, which defeats
        defense in depth.
        """
        denylist_patch.return_value = ["internal_id"]
        provider = _NoisyProvider()
        chunks = await provider.retrieve("ns", "q", include_citations=False)
        assert "internal_id" not in chunks[0].metadata


# ── Integration: LlamaIndex + AgentCore populate citations ───────────


class TestLlamaIndexCitations:
    """LlamaIndex-specific: providers that CAN produce structured sources
    must do so when asked.  We only test the happy shape — the generic
    flag plumbing is covered above.
    """

    @pytest.fixture
    def mock_embed_settings(self) -> Generator[None]:
        try:
            from llama_index.core import Settings as LlamaSettings
            from llama_index.core.embeddings.mock_embed_model import MockEmbedding
        except ImportError:
            pytest.skip("LlamaIndex not installed")
        LlamaSettings.embed_model = MockEmbedding(embed_dim=8)
        yield

    async def test_citations_populated_when_requested(self, mock_embed_settings: None) -> None:
        from agentic_primitives_gateway.primitives.knowledge.llamaindex import LlamaIndexKnowledgeProvider

        provider = LlamaIndexKnowledgeProvider(store_type="vector")
        await provider.ingest(
            "ns",
            [IngestDocument(text="Paris is a city.", metadata={"topic": "geo"}, source="geo.md")],
        )

        chunks = await provider.retrieve("ns", "Paris", top_k=1, include_citations=True)
        assert chunks
        assert chunks[0].citations is not None
        citation = chunks[0].citations[0]
        assert citation.source == "geo.md"
        # Snippet is bounded; text is preserved on the chunk itself.
        assert citation.snippet is not None
        assert chunks[0].text == "Paris is a city."

    async def test_citations_none_when_not_requested(self, mock_embed_settings: None) -> None:
        from agentic_primitives_gateway.primitives.knowledge.llamaindex import LlamaIndexKnowledgeProvider

        provider = LlamaIndexKnowledgeProvider(store_type="vector")
        await provider.ingest("ns", [IngestDocument(text="hello", source="x.md")])
        chunks = await provider.retrieve("ns", "hello", top_k=1)
        assert chunks[0].citations is None


class TestAgentCoreCitations:
    """Bedrock KB citations: when requested, populate S3 URIs + page info
    from the ``location`` block and KB metadata.
    """

    @patch("agentic_primitives_gateway.primitives.knowledge.agentcore.get_boto3_session")
    @patch("agentic_primitives_gateway.primitives.knowledge.agentcore.get_service_credentials")
    async def test_s3_location_becomes_citation_uri(self, mock_svc_creds: MagicMock, mock_session: MagicMock) -> None:
        from agentic_primitives_gateway.primitives.knowledge.agentcore import AgentCoreKnowledgeProvider

        mock_svc_creds.return_value = {"knowledgebase_id": "kb"}
        runtime = MagicMock()
        runtime.retrieve.return_value = {
            "retrievalResults": [
                {
                    "content": {"text": "chunk body"},
                    "location": {"s3Location": {"uri": "s3://bucket/doc.pdf"}},
                    "metadata": {"x-amz-bedrock-kb-document-page-number": 7},
                    "score": 0.9,
                }
            ]
        }
        sess = MagicMock(region_name="us-east-1")
        sess.client.return_value = runtime
        mock_session.return_value = sess

        provider = AgentCoreKnowledgeProvider()
        chunks = await provider.retrieve("ns", "q", top_k=1, include_citations=True)

        assert chunks[0].citations is not None
        citation = chunks[0].citations[0]
        assert citation.uri == "s3://bucket/doc.pdf"
        assert citation.source == "s3://bucket/doc.pdf"
        assert citation.page == "7"
