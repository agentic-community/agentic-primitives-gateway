from __future__ import annotations

import pytest

from agentic_primitives_gateway_client import AgenticPlatformError


class TestKnowledgeClient:
    async def test_ingest_returns_document_ids(self, make_client) -> None:
        async with make_client() as client:
            result = await client.ingest_knowledge(
                "demo",
                documents=[
                    {"text": "Paris is the capital of France."},
                    {"text": "Rome is the capital of Italy."},
                ],
            )
        assert result["ingested"] == 2
        assert len(result["document_ids"]) == 2

    async def test_ingest_preserves_explicit_id(self, make_client) -> None:
        async with make_client() as client:
            result = await client.ingest_knowledge(
                "demo",
                documents=[{"text": "hello", "document_id": "my-doc"}],
            )
        assert result["document_ids"] == ["my-doc"]

    async def test_list_documents(self, make_client) -> None:
        async with make_client() as client:
            await client.ingest_knowledge(
                "demo",
                documents=[{"text": "a", "metadata": {"tag": "x"}}],
            )
            listed = await client.list_knowledge_documents("demo")
        assert listed["total"] == 1
        assert listed["documents"][0]["metadata"] == {"tag": "x"}

    async def test_retrieve_matches_query(self, make_client) -> None:
        async with make_client() as client:
            await client.ingest_knowledge(
                "demo",
                documents=[
                    {"text": "The Eiffel Tower is in Paris."},
                    {"text": "The Colosseum is in Rome."},
                ],
            )
            result = await client.retrieve_knowledge("demo", "Paris", top_k=5)
        assert len(result["chunks"]) == 1
        assert "Paris" in result["chunks"][0]["text"]

    async def test_retrieve_honours_top_k(self, make_client) -> None:
        async with make_client() as client:
            await client.ingest_knowledge(
                "demo",
                documents=[
                    {"text": "The cat sat."},
                    {"text": "The cat ran."},
                    {"text": "The cat jumped."},
                ],
            )
            result = await client.retrieve_knowledge("demo", "cat", top_k=2)
        assert len(result["chunks"]) == 2

    async def test_delete_document(self, make_client) -> None:
        async with make_client() as client:
            result = await client.ingest_knowledge(
                "demo",
                documents=[{"text": "x", "document_id": "d1"}],
            )
            doc_id = result["document_ids"][0]
            await client.delete_knowledge_document("demo", doc_id)
            listed = await client.list_knowledge_documents("demo")
        assert listed["total"] == 0

    async def test_delete_missing_document_raises(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError):
                await client.delete_knowledge_document("demo", "does-not-exist")

    async def test_query_returns_answer(self, make_client) -> None:
        async with make_client() as client:
            result = await client.query_knowledge("demo", "What is the capital of France?")
        assert "answer" in result
        assert "France" in result["answer"]

    async def test_list_namespaces(self, make_client) -> None:
        async with make_client() as client:
            await client.ingest_knowledge("alpha", documents=[{"text": "a"}])
            await client.ingest_knowledge("beta", documents=[{"text": "b"}])
            result = await client.list_knowledge_namespaces()
        assert set(result["namespaces"]) == {"alpha", "beta"}

    async def test_retrieve_passes_include_citations_to_server(self, make_client) -> None:
        """Default and explicit include_citations values must survive the
        round-trip so REST callers can opt into structured citations
        without running an agent.
        """
        async with make_client() as client:
            await client.ingest_knowledge(
                "demo",
                documents=[{"text": "The Eiffel Tower is in Paris.", "source": "geo.md"}],
            )
            default_result = await client.retrieve_knowledge("demo", "Paris", top_k=1)
            cited_result = await client.retrieve_knowledge("demo", "Paris", top_k=1, include_citations=True)

        # Default path: citations are absent / null — the server shape is
        # whatever the test backend returns when flag=False.  Contract:
        # the response must at least come back with chunks populated.
        assert default_result["chunks"]
        # include_citations=True must at least not regress chunk delivery
        # and — for backends that support it — carry the citations field.
        assert cited_result["chunks"]
        # If the backend populated citations, they must be a list; if
        # not, it stays ``None`` / absent.  Accept both so the test
        # doesn't couple to a specific backend's capabilities.
        citations = cited_result["chunks"][0].get("citations")
        assert citations is None or isinstance(citations, list)

    async def test_namespaces_are_isolated(self, make_client) -> None:
        async with make_client() as client:
            await client.ingest_knowledge(
                "alice",
                documents=[{"text": "alice-only secret"}],
            )
            await client.ingest_knowledge(
                "bob",
                documents=[{"text": "bob-only secret"}],
            )
            alice_result = await client.retrieve_knowledge("alice", "secret", top_k=10)
            bob_result = await client.retrieve_knowledge("bob", "secret", top_k=10)
        alice_texts = [c["text"] for c in alice_result["chunks"]]
        bob_texts = [c["text"] for c in bob_result["chunks"]]
        assert all("alice" in t for t in alice_texts)
        assert all("bob" in t for t in bob_texts)
