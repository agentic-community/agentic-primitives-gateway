from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.models.knowledge import IngestDocument
from agentic_primitives_gateway.primitives.knowledge.agentcore import AgentCoreKnowledgeProvider


@patch("agentic_primitives_gateway.primitives.knowledge.agentcore.get_boto3_session")
@patch("agentic_primitives_gateway.primitives.knowledge.agentcore.get_service_credentials")
class TestAgentCoreKnowledgeProvider:
    def _provider(self, **kwargs):
        return AgentCoreKnowledgeProvider(**kwargs)

    async def test_retrieve_happy_path(self, mock_svc_creds, mock_session) -> None:
        mock_svc_creds.return_value = {"knowledgebase_id": "kb-abc"}
        runtime = MagicMock()
        runtime.retrieve.return_value = {
            "retrievalResults": [
                {
                    "content": {"text": "chunk-one"},
                    "location": {"s3Location": {"uri": "s3://bucket/doc1.pdf"}},
                    "metadata": {"topic": "x"},
                    "score": 0.92,
                },
                {
                    "content": {"text": "chunk-two"},
                    "location": {"s3Location": {"uri": "s3://bucket/doc2.pdf"}},
                    "metadata": {"topic": "y"},
                    "score": 0.71,
                },
            ]
        }
        sess = MagicMock(region_name="us-east-1")
        sess.client.return_value = runtime
        mock_session.return_value = sess

        provider = self._provider()
        chunks = await provider.retrieve("ns", "hello", top_k=5)

        sess.client.assert_called_with("bedrock-agent-runtime")
        runtime.retrieve.assert_called_once_with(
            knowledgeBaseId="kb-abc",
            retrievalQuery={"text": "hello"},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 5}},
        )
        assert [c.text for c in chunks] == ["chunk-one", "chunk-two"]
        assert chunks[0].score == pytest.approx(0.92)
        assert chunks[0].metadata["source"] == "s3://bucket/doc1.pdf"

    async def test_retrieve_prefers_header_over_config(self, mock_svc_creds, mock_session) -> None:
        mock_svc_creds.return_value = {"knowledgebase_id": "from-header"}
        runtime = MagicMock()
        runtime.retrieve.return_value = {"retrievalResults": []}
        sess = MagicMock(region_name="us-east-1")
        sess.client.return_value = runtime
        mock_session.return_value = sess

        provider = self._provider(knowledge_base_id="from-config")
        await provider.retrieve("ns", "q", top_k=1)

        called_kb = runtime.retrieve.call_args.kwargs["knowledgeBaseId"]
        assert called_kb == "from-header"

    async def test_retrieve_missing_kb_raises(self, mock_svc_creds, mock_session) -> None:
        mock_svc_creds.return_value = None
        mock_session.return_value = MagicMock()

        provider = self._provider()
        with pytest.raises(ValueError, match="knowledge_base_id"):
            await provider.retrieve("ns", "q")

    async def test_query_uses_retrieve_and_generate(self, mock_svc_creds, mock_session) -> None:
        mock_svc_creds.return_value = {"knowledgebase_id": "kb-xyz"}
        runtime = MagicMock()
        runtime.retrieve_and_generate.return_value = {
            "output": {"text": "synthesized answer"},
            "citations": [
                {
                    "retrievedReferences": [
                        {
                            "content": {"text": "source chunk"},
                            "location": {"s3Location": {"uri": "s3://b/d.pdf"}},
                            "metadata": {"page": 3},
                            "chunkId": "c-1",
                        }
                    ]
                }
            ],
        }
        sess = MagicMock(region_name="us-east-1")
        sess.client.return_value = runtime
        mock_session.return_value = sess

        provider = self._provider(default_model_arn="arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude")
        response = await provider.query("ns", "what?", top_k=2)

        assert response.answer == "synthesized answer"
        assert len(response.chunks) == 1
        assert response.chunks[0].chunk_id == "c-1"
        assert response.chunks[0].metadata["source"] == "s3://b/d.pdf"

    async def test_query_without_model_arn_is_not_implemented(self, mock_svc_creds, mock_session) -> None:
        mock_svc_creds.return_value = {"knowledgebase_id": "kb-xyz"}
        mock_session.return_value = MagicMock()

        provider = self._provider()
        with pytest.raises(NotImplementedError, match="default_model_arn"):
            await provider.query("ns", "what?")

    async def test_ingest_without_data_source_is_not_implemented(self, mock_svc_creds, mock_session) -> None:
        mock_svc_creds.return_value = {"knowledgebase_id": "kb"}
        mock_session.return_value = MagicMock()

        provider = self._provider()
        with pytest.raises(NotImplementedError, match="data_source_id"):
            await provider.ingest("ns", [IngestDocument(text="x")])

    async def test_ingest_triggers_sync_when_configured(self, mock_svc_creds, mock_session) -> None:
        mock_svc_creds.return_value = {
            "knowledgebase_id": "kb-a",
            "data_source_id": "ds-b",
        }
        control = MagicMock()
        control.start_ingestion_job.return_value = {"ingestionJob": {"ingestionJobId": "job-1"}}
        sess = MagicMock(region_name="us-east-1")
        sess.client.return_value = control
        mock_session.return_value = sess

        provider = self._provider()
        result = await provider.ingest("ns", [IngestDocument(text="x")])

        sess.client.assert_called_with("bedrock-agent")
        control.start_ingestion_job.assert_called_once()
        assert result.document_ids == ["job-1"]
        assert result.ingested == 0  # sync triggered, not documents uploaded

    async def test_delete_raises_not_implemented(self, mock_svc_creds, mock_session) -> None:
        mock_svc_creds.return_value = {"knowledgebase_id": "kb"}
        mock_session.return_value = MagicMock()

        provider = self._provider()
        with pytest.raises(NotImplementedError):
            await provider.delete("ns", "doc")

    async def test_list_documents_raises_not_implemented(self, mock_svc_creds, mock_session) -> None:
        mock_svc_creds.return_value = {"knowledgebase_id": "kb"}
        mock_session.return_value = MagicMock()

        provider = self._provider()
        with pytest.raises(NotImplementedError):
            await provider.list_documents("ns")


class TestAgentCoreDoesNotRouteThroughRegistryLLM:
    """Intent: AgentCore ``query()`` intentionally bypasses ``registry.llm``.

    Per the module docstring: "this path bypasses ``registry.llm``
    because the KB owns the model."  The operator configures the
    synthesis model via ``default_model_arn`` and Bedrock runs it
    server-side — that's the whole value of native retrieve-and-generate.

    If someone "fixes" this for consistency with the LlamaIndex backend
    (which DOES route through ``registry.llm``), the operator's model
    ARN config silently stops being used and the synthesis path changes
    underneath them.

    A tautological patch-based assertion ("the code that doesn't import
    registry.llm didn't call registry.llm") would pass even if the
    entire ``query()`` method were deleted.  Instead this test guards
    the *structural* property: the ``agentcore`` module must not import
    the gateway registry at all.  ``inspect.getsource`` on the module
    includes every method body too, so lazy imports inside ``query()``
    (or anywhere else) are caught by the same check.
    """

    def test_agentcore_module_does_not_import_registry(self) -> None:
        import inspect

        from agentic_primitives_gateway.primitives.knowledge import agentcore

        source = inspect.getsource(agentcore)
        # Guard both the common import forms.  Substring matches are
        # fine here — the module is small and a false positive would
        # itself be worth investigating.
        assert "from agentic_primitives_gateway.registry" not in source, (
            "AgentCoreKnowledgeProvider must NOT import the gateway registry — "
            "query() intentionally bypasses registry.llm (see module docstring)."
        )
        assert "import agentic_primitives_gateway.registry" not in source, (
            "AgentCoreKnowledgeProvider must NOT import the gateway registry — "
            "query() intentionally bypasses registry.llm (see module docstring)."
        )
