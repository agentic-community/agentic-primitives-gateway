from __future__ import annotations

from collections.abc import Generator

import pytest

from agentic_primitives_gateway.models.knowledge import IngestDocument
from agentic_primitives_gateway.primitives.knowledge.llamaindex import (
    LlamaIndexKnowledgeProvider,
)

# Use LlamaIndex's MockEmbedding so tests don't need OpenAI/Bedrock creds.
# We don't try to restore the previous embed model — reading
# ``Settings.embed_model`` lazily instantiates OpenAIEmbedding which fails
# without OPENAI_API_KEY.  Overwriting is enough; no other tests rely on
# the default.


@pytest.fixture
def mock_embed_settings() -> Generator[None]:
    from llama_index.core import Settings
    from llama_index.core.embeddings.mock_embed_model import MockEmbedding

    Settings.embed_model = MockEmbedding(embed_dim=8)
    yield


@pytest.fixture
def provider(mock_embed_settings: None) -> LlamaIndexKnowledgeProvider:
    return LlamaIndexKnowledgeProvider(store_type="vector")


@pytest.fixture
def docs() -> list[IngestDocument]:
    return [
        IngestDocument(text="The Eiffel Tower is in Paris.", metadata={"topic": "landmarks"}),
        IngestDocument(text="The Colosseum is in Rome.", metadata={"topic": "landmarks"}),
        IngestDocument(text="Paris has excellent pastries.", metadata={"topic": "food"}),
    ]


class TestStoreType:
    def test_valid_store_types(self, mock_embed_settings: None) -> None:
        for st in ("vector", "graph", "hybrid"):
            p = LlamaIndexKnowledgeProvider(store_type=st)
            assert p.store_type == st

    def test_invalid_store_type_raises(self, mock_embed_settings: None) -> None:
        with pytest.raises(ValueError, match="Unknown store_type"):
            LlamaIndexKnowledgeProvider(store_type="quantum")


class TestIngest:
    async def test_ingest_assigns_ids(self, provider: LlamaIndexKnowledgeProvider, docs: list[IngestDocument]) -> None:
        result = await provider.ingest("ns1", docs)
        assert result.ingested == 3
        assert len(result.document_ids) == 3
        assert all(doc_id for doc_id in result.document_ids)

    async def test_ingest_preserves_explicit_id(self, provider: LlamaIndexKnowledgeProvider) -> None:
        result = await provider.ingest(
            "ns1",
            [IngestDocument(text="hello", document_id="my-doc-1")],
        )
        assert result.document_ids == ["my-doc-1"]

    async def test_ingest_empty_list(self, provider: LlamaIndexKnowledgeProvider) -> None:
        result = await provider.ingest("ns1", [])
        assert result.ingested == 0
        assert result.document_ids == []


class TestRetrieve:
    async def test_retrieve_returns_chunks(
        self, provider: LlamaIndexKnowledgeProvider, docs: list[IngestDocument]
    ) -> None:
        await provider.ingest("ns1", docs)
        chunks = await provider.retrieve("ns1", "Paris", top_k=3)
        assert len(chunks) > 0
        # Every chunk should carry the text of one of the ingested docs.
        texts = {d.text for d in docs}
        for c in chunks:
            assert c.text in texts

    async def test_retrieve_honours_top_k(
        self, provider: LlamaIndexKnowledgeProvider, docs: list[IngestDocument]
    ) -> None:
        await provider.ingest("ns1", docs)
        chunks = await provider.retrieve("ns1", "landmark", top_k=1)
        assert len(chunks) == 1

    async def test_retrieve_strips_internal_metadata_keys(
        self, provider: LlamaIndexKnowledgeProvider, docs: list[IngestDocument]
    ) -> None:
        await provider.ingest("ns1", docs)
        chunks = await provider.retrieve("ns1", "Paris", top_k=1)
        assert chunks
        for key in chunks[0].metadata:
            assert not key.startswith("_apg_")

    async def test_retrieve_isolates_namespaces(self, provider: LlamaIndexKnowledgeProvider) -> None:
        await provider.ingest("alice", [IngestDocument(text="alice secret")])
        await provider.ingest("bob", [IngestDocument(text="bob secret")])
        chunks = await provider.retrieve("alice", "secret", top_k=5)
        assert chunks  # at least one chunk
        for c in chunks:
            assert c.text == "alice secret"


class TestListAndDelete:
    async def test_list_documents_reflects_ingests(
        self, provider: LlamaIndexKnowledgeProvider, docs: list[IngestDocument]
    ) -> None:
        await provider.ingest("ns1", docs)
        listed = await provider.list_documents("ns1")
        assert len(listed) == 3

    async def test_list_documents_paginates(
        self, provider: LlamaIndexKnowledgeProvider, docs: list[IngestDocument]
    ) -> None:
        await provider.ingest("ns1", docs)
        first = await provider.list_documents("ns1", limit=1, offset=0)
        second = await provider.list_documents("ns1", limit=1, offset=1)
        assert len(first) == 1
        assert len(second) == 1
        assert first[0].document_id != second[0].document_id

    async def test_list_namespaces(self, provider: LlamaIndexKnowledgeProvider) -> None:
        await provider.ingest("alpha", [IngestDocument(text="a")])
        await provider.ingest("beta", [IngestDocument(text="b")])
        namespaces = await provider.list_namespaces()
        assert set(namespaces) == {"alpha", "beta"}

    async def test_delete_existing_document(
        self, provider: LlamaIndexKnowledgeProvider, docs: list[IngestDocument]
    ) -> None:
        result = await provider.ingest("ns1", docs)
        first_id = result.document_ids[0]
        assert (await provider.delete("ns1", first_id)) is True
        listed = await provider.list_documents("ns1")
        assert all(d.document_id != first_id for d in listed)

    async def test_delete_missing_document_returns_false(self, provider: LlamaIndexKnowledgeProvider) -> None:
        assert (await provider.delete("ns1", "does-not-exist")) is False
