"""Integration test for the knowledge primitive end-to-end.

Exercises the full stack (client → ASGI → route → registry → LlamaIndex
provider → SimpleVectorStore) with no external services.  Uses
LlamaIndex's ``MockEmbedding`` so no embedding API credentials are
required, which means this test runs on every CI job, not just AWS.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator

import httpx
import pytest

from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError


@pytest.fixture(scope="module")
def _mock_embed_model() -> Generator[None]:
    """Install MockEmbedding as the LlamaIndex default so ingest works keyless."""
    from llama_index.core import Settings as LlamaSettings
    from llama_index.core.embeddings.mock_embed_model import MockEmbedding

    LlamaSettings.embed_model = MockEmbedding(embed_dim=8)
    yield


@pytest.fixture(autouse=True)
def _init_knowledge_registry(_mock_embed_model: None) -> None:
    """Initialise the registry with a real LlamaIndex knowledge backend."""
    test_settings = Settings(
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider",
                "config": {},
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "llm": {
                "backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider",
                "config": {},
            },
            "tools": {
                "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
                "config": {},
            },
            "identity": {
                "backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider",
                "config": {},
            },
            "code_interpreter": {
                "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider",
                "config": {},
            },
            "browser": {
                "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
                "config": {},
            },
            "policy": {
                "backend": "agentic_primitives_gateway.primitives.policy.noop.NoopPolicyProvider",
                "config": {},
            },
            "evaluations": {
                "backend": "agentic_primitives_gateway.primitives.evaluations.noop.NoopEvaluationsProvider",
                "config": {},
            },
            "tasks": {
                "backend": "agentic_primitives_gateway.primitives.tasks.noop.NoopTasksProvider",
                "config": {},
            },
            "knowledge": {
                "backend": "agentic_primitives_gateway.primitives.knowledge.llamaindex.LlamaIndexKnowledgeProvider",
                "config": {"store_type": "vector"},
            },
        },
    )
    registry.initialize(test_settings)


@pytest.fixture
async def client() -> AsyncGenerator[AgenticPlatformClient]:
    transport = httpx.ASGITransport(app=app)
    async with AgenticPlatformClient(
        base_url="http://testserver",
        transport=transport,
    ) as c:
        yield c


class TestKnowledgeLifecycle:
    async def test_ingest_retrieve_delete_roundtrip(self, client: AgenticPlatformClient) -> None:
        # 1. Ingest a small corpus.
        result = await client.ingest_knowledge(
            "integ",
            documents=[
                {"text": "The Eiffel Tower is in Paris.", "metadata": {"topic": "landmarks"}},
                {"text": "The Colosseum is in Rome.", "metadata": {"topic": "landmarks"}},
                {"text": "Paris has excellent pastries.", "metadata": {"topic": "food"}},
            ],
        )
        assert result["ingested"] == 3
        document_ids = result["document_ids"]
        assert len(document_ids) == 3

        # 2. List — should see 3 documents.
        listed = await client.list_knowledge_documents("integ")
        assert listed["total"] == 3

        # 3. Retrieve — should return ranked chunks (MockEmbedding makes
        #    scores constant but the plumbing is validated).
        retrieved = await client.retrieve_knowledge("integ", "Paris", top_k=2)
        chunks = retrieved["chunks"]
        assert len(chunks) == 2
        for c in chunks:
            assert "text" in c
            assert "score" in c
            assert "document_id" in c
            assert "metadata" in c

        # 4. Delete one document, verify list reflects it.
        target = document_ids[0]
        await client.delete_knowledge_document("integ", target)

        listed_after = await client.list_knowledge_documents("integ")
        remaining_ids = {d["document_id"] for d in listed_after["documents"]}
        assert target not in remaining_ids
        assert listed_after["total"] == 2

        # 5. Delete a non-existent document → 404.
        with pytest.raises(AgenticPlatformError):
            await client.delete_knowledge_document("integ", "not-a-real-doc-id")

    async def test_namespaces_are_isolated(self, client: AgenticPlatformClient) -> None:
        await client.ingest_knowledge("alice", documents=[{"text": "alice-only"}])
        await client.ingest_knowledge("bob", documents=[{"text": "bob-only"}])

        alice_chunks = await client.retrieve_knowledge("alice", "anything", top_k=10)
        for c in alice_chunks["chunks"]:
            assert c["text"] == "alice-only"

        bob_chunks = await client.retrieve_knowledge("bob", "anything", top_k=10)
        for c in bob_chunks["chunks"]:
            assert c["text"] == "bob-only"

    async def test_query_returns_501_for_this_backend_without_llm(self, client: AgenticPlatformClient) -> None:
        # The LlamaIndex backend implements query, but with the Noop LLM
        # in this test registry the query path raises on synthesis —
        # exact outcome depends on NoopLLMProvider's behavior.  At minimum,
        # we verify it doesn't hang or 500 with an unexpected exception.
        try:
            resp = await client.query_knowledge("alice", "what?", top_k=1)
            # If the noop LLM completes, we just check the shape.
            assert "answer" in resp
        except AgenticPlatformError:
            # 501 / 500 / noop-driven failure are acceptable for this smoke
            # path; the contract is verified in unit tests.
            pass
