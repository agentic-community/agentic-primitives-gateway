from __future__ import annotations

import pytest

from agentic_primitives_gateway.models.knowledge import IngestDocument
from agentic_primitives_gateway.primitives.knowledge.noop import NoopKnowledgeProvider


@pytest.fixture
def provider() -> NoopKnowledgeProvider:
    return NoopKnowledgeProvider()


class TestNoopKnowledgeProvider:
    async def test_ingest_returns_zero_ingested(self, provider: NoopKnowledgeProvider) -> None:
        docs = [IngestDocument(text="hello", metadata={"k": "v"})]
        result = await provider.ingest("ns1", docs)
        assert result.ingested == 0

    async def test_retrieve_returns_empty(self, provider: NoopKnowledgeProvider) -> None:
        chunks = await provider.retrieve("ns1", "query", top_k=5)
        assert chunks == []

    async def test_delete_returns_false(self, provider: NoopKnowledgeProvider) -> None:
        assert (await provider.delete("ns1", "doc1")) is False

    async def test_list_documents_returns_empty(self, provider: NoopKnowledgeProvider) -> None:
        assert (await provider.list_documents("ns1")) == []

    async def test_list_namespaces_returns_empty(self, provider: NoopKnowledgeProvider) -> None:
        assert (await provider.list_namespaces()) == []

    async def test_healthcheck_returns_true(self, provider: NoopKnowledgeProvider) -> None:
        assert (await provider.healthcheck()) is True

    async def test_query_not_implemented(self, provider: NoopKnowledgeProvider) -> None:
        with pytest.raises(NotImplementedError):
            await provider.query("ns1", "question")

    async def test_store_type_is_noop(self, provider: NoopKnowledgeProvider) -> None:
        assert provider.store_type == "noop"
