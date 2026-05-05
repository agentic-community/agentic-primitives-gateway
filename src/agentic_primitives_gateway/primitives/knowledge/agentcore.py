"""AgentCore Knowledge Bases provider.

Wraps ``bedrock-agent-runtime`` for retrieval and native
retrieve-and-generate.  ``knowledge_base_id`` is resolved per-request
from service credentials (``X-Cred-Agentcore-Knowledgebase-Id``) so a
single provider instance serves many users with distinct KBs.

``query()`` uses the native ``retrieve_and_generate`` API — note that
this path bypasses ``registry.llm`` because the KB owns the model.  If
uniform LLM routing matters more than the one-shot convenience, use
``retrieve()`` through this provider and feed the chunks into the
gateway's LLM primitive yourself.
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.context import get_boto3_session, get_service_credentials
from agentic_primitives_gateway.models.knowledge import (
    Citation,
    DocumentInfo,
    IngestDocument,
    IngestResult,
    QueryResponse,
    RetrievedChunk,
)
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.knowledge.base import KnowledgeProvider

logger = logging.getLogger(__name__)


def _build_agentcore_citations(
    item: dict[str, Any],
    location: dict[str, Any],
    metadata: dict[str, Any],
    text: str,
) -> list[Citation]:
    """Build structured citations from a Bedrock KB retrieval result.

    Bedrock KB ``retrievalResults`` carry a ``location`` block with
    per-source-type URIs (S3, Confluence, SharePoint, Salesforce, Web)
    and a free-form ``metadata`` dict operators attach at ingest.  We
    surface the most useful fields into named Citation slots and copy
    the rest of ``metadata`` as passthrough so the UI can render
    provider-specific keys without the ABC knowing about them.
    """
    source = metadata.get("source")
    uri: str | None = None

    # Walk the common location shapes.  Bedrock uses different key names
    # per source type so we try each in priority order rather than
    # assuming the S3 shape.
    for loc_key, uri_key in (
        ("s3Location", "uri"),
        ("webLocation", "url"),
        ("confluenceLocation", "url"),
        ("salesforceLocation", "url"),
        ("sharePointLocation", "url"),
    ):
        loc = location.get(loc_key) if isinstance(location, dict) else None
        if isinstance(loc, dict) and loc.get(uri_key):
            uri = str(loc[uri_key])
            break

    if not source and uri:
        source = uri

    page_value = metadata.get("x-amz-bedrock-kb-document-page-number") or metadata.get("page")
    page_str = str(page_value) if page_value is not None else None

    snippet = text[:200] if text else None

    # metadata passthrough minus fields we already surfaced
    passthrough = {
        k: v for k, v in metadata.items() if k not in {"source", "page", "x-amz-bedrock-kb-document-page-number"}
    }

    return [
        Citation(
            source=str(source) if source else None,
            uri=uri,
            page=page_str,
            span=None,
            snippet=snippet,
            metadata=passthrough,
        )
    ]


class AgentCoreKnowledgeProvider(SyncRunnerMixin, KnowledgeProvider):
    """Knowledge provider backed by AWS Bedrock Knowledge Bases.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.knowledge.agentcore.AgentCoreKnowledgeProvider
        config:
          region: "us-east-1"
          knowledge_base_id: "ABCDEF1234"   # optional — overridden per-request
          default_model_arn: "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-3-5-sonnet-20241022-v2:0"
          data_source_id: "XYZ987"          # optional — required for ingest() sync
    """

    store_type = "native"

    def __init__(
        self,
        *,
        region: str = "us-east-1",
        knowledge_base_id: str | None = None,
        default_model_arn: str | None = None,
        data_source_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._region = region
        self._default_kb_id = knowledge_base_id
        self._default_model_arn = default_model_arn
        self._default_data_source_id = data_source_id
        logger.info(
            "AgentCoreKnowledgeProvider initialized (region=%s, default_kb=%s)",
            region,
            knowledge_base_id or "(from client)",
        )

    def _resolve_knowledge_base_id(self) -> str:
        creds = get_service_credentials("agentcore")
        if creds and creds.get("knowledgebase_id"):
            kb_id: str = creds["knowledgebase_id"]
            return kb_id
        if self._default_kb_id:
            return self._default_kb_id
        raise ValueError(
            "AgentCore knowledge_base_id is required.  Provide it via one of: "
            "(1) per-user OIDC attribute apg.agentcore.knowledgebase_id (resolved "
            "into the X-Cred-Agentcore-Knowledgebase-Id header for each request); "
            "(2) knowledge_base_id in the provider config (AGENTCORE_KB_ID env var "
            "in shipped configs); "
            "(3) define multiple named AgentCore backends in providers.knowledge.backends "
            "and pin one from an agent spec via spec.provider_overrides.knowledge. "
            "Create a Knowledge Base in the AWS console first."
        )

    def _resolve_data_source_id(self) -> str | None:
        creds = get_service_credentials("agentcore")
        if creds and creds.get("data_source_id"):
            ds_id: str = creds["data_source_id"]
            return ds_id
        return self._default_data_source_id

    def _runtime_client(self) -> Any:
        session = get_boto3_session(default_region=self._region)
        return session.client("bedrock-agent-runtime")

    def _control_client(self) -> Any:
        session = get_boto3_session(default_region=self._region)
        return session.client("bedrock-agent")

    # ── Primitive methods ──────────────────────────────────────────────

    async def ingest(
        self,
        namespace: str,
        documents: list[IngestDocument],
    ) -> IngestResult:
        """Kick off a Bedrock Knowledge Base data-source sync.

        The Knowledge Base itself owns the underlying data store (S3,
        etc.) — the gateway surfaces only the sync-trigger here as a v1
        convenience.  Upload documents to the KB's backing store through
        your usual pipeline; ``ingest()`` with a non-empty document list
        is accepted but the documents are *not* uploaded, only recorded
        in the ingestion-job metadata.
        """
        data_source_id = self._resolve_data_source_id()
        if not data_source_id:
            # Same operator-scope resolution shape as the KB id.
            raise NotImplementedError(
                "AgentCoreKnowledgeProvider.ingest requires data_source_id. "
                "Provide it via one of: "
                "(1) per-user OIDC attribute apg.agentcore.data_source_id "
                "(resolved into X-Cred-Agentcore-Data-Source-Id per request); "
                "(2) data_source_id in the provider config; "
                "(3) a named AgentCore backend pinned via spec.provider_overrides.knowledge. "
                "Upload documents to the KB's backing store (e.g. S3) separately."
            )
        kb_id = self._resolve_knowledge_base_id()
        client = self._control_client()

        def _start() -> Any:
            return client.start_ingestion_job(
                knowledgeBaseId=kb_id,
                dataSourceId=data_source_id,
                description=f"apg:ingest namespace={namespace} count={len(documents)}",
            )

        response = await self._run_sync(_start)
        job = response.get("ingestionJob", {}) if isinstance(response, dict) else {}
        job_id = job.get("ingestionJobId") or ""
        return IngestResult(
            document_ids=[job_id] if job_id else [],
            ingested=0,
        )

    async def retrieve(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        *,
        include_citations: bool = False,
    ) -> list[RetrievedChunk]:
        kb_id = self._resolve_knowledge_base_id()
        client = self._runtime_client()

        retrieval_config: dict[str, Any] = {
            "vectorSearchConfiguration": {"numberOfResults": top_k},
        }
        if filters:
            # KB metadata filters use a slightly different shape; pass
            # through as-is so callers who know the shape can use them,
            # and silently skip if empty.
            retrieval_config["vectorSearchConfiguration"]["filter"] = filters

        def _retrieve() -> Any:
            return client.retrieve(
                knowledgeBaseId=kb_id,
                retrievalQuery={"text": query},
                retrievalConfiguration=retrieval_config,
            )

        response = await self._run_sync(_retrieve)
        results = response.get("retrievalResults", []) if isinstance(response, dict) else []
        chunks: list[RetrievedChunk] = []
        for idx, item in enumerate(results):
            content = item.get("content", {}) or {}
            text = content.get("text", "") or ""
            location = item.get("location", {}) or {}
            metadata = dict(item.get("metadata", {}) or {})
            # S3 location is the common case; surface it as source.
            s3 = location.get("s3Location", {}) if isinstance(location, dict) else {}
            if s3.get("uri"):
                metadata.setdefault("source", s3["uri"])
            citations = _build_agentcore_citations(item, location, metadata, text) if include_citations else None
            chunks.append(
                RetrievedChunk(
                    chunk_id=f"{namespace}:{idx}",
                    document_id=str(metadata.get("source") or ""),
                    text=text,
                    score=float(item.get("score", 0.0) or 0.0),
                    metadata=metadata,
                    citations=citations,
                )
            )
        return chunks

    async def query(
        self,
        namespace: str,
        question: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> QueryResponse:
        kb_id = self._resolve_knowledge_base_id()
        model_arn = self._default_model_arn
        if not model_arn:
            raise NotImplementedError(
                "AgentCoreKnowledgeProvider.query requires default_model_arn "
                "in the provider config (e.g. an Anthropic foundation-model ARN)."
            )
        client = self._runtime_client()

        retrieve_and_generate_config: dict[str, Any] = {
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": kb_id,
                "modelArn": model_arn,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {"numberOfResults": top_k},
                },
            },
        }
        if filters:
            retrieve_and_generate_config["knowledgeBaseConfiguration"]["retrievalConfiguration"][
                "vectorSearchConfiguration"
            ]["filter"] = filters

        def _query() -> Any:
            return client.retrieve_and_generate(
                input={"text": question},
                retrieveAndGenerateConfiguration=retrieve_and_generate_config,
            )

        response = await self._run_sync(_query)
        output = response.get("output", {}) if isinstance(response, dict) else {}
        answer = output.get("text", "") or ""

        chunks: list[RetrievedChunk] = []
        citations = response.get("citations", []) if isinstance(response, dict) else []
        for citation in citations:
            for ref in citation.get("retrievedReferences", []) or []:
                content = ref.get("content", {}) or {}
                location = ref.get("location", {}) or {}
                metadata = dict(ref.get("metadata", {}) or {})
                s3 = location.get("s3Location", {}) if isinstance(location, dict) else {}
                if s3.get("uri"):
                    metadata.setdefault("source", s3["uri"])
                chunks.append(
                    RetrievedChunk(
                        chunk_id=str(ref.get("chunkId") or ""),
                        document_id=str(metadata.get("source") or ""),
                        text=content.get("text", "") or "",
                        score=0.0,
                        metadata=metadata,
                    )
                )

        return QueryResponse(answer=answer, chunks=chunks)

    async def delete(self, namespace: str, document_id: str) -> bool:
        # AgentCore Knowledge Bases don't expose per-document delete via
        # the runtime API — documents are governed by the backing data
        # source (S3, etc.).  Delete from the backing store, then trigger
        # a re-sync via ``ingest()``.
        raise NotImplementedError(
            "AgentCoreKnowledgeProvider does not support per-document delete. "
            "Remove the source document from the backing data store (e.g. S3) "
            "and trigger a Knowledge Base sync."
        )

    async def list_documents(
        self,
        namespace: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentInfo]:
        # No runtime-side listing; surface as not-implemented so the
        # route layer translates to 501.
        raise NotImplementedError(
            "AgentCoreKnowledgeProvider does not expose document listing. Inspect the backing data source directly."
        )

    async def healthcheck(self) -> bool:
        # Let exceptions propagate so the audit/metrics wrapper sees the
        # failure.  The route layer already catches and maps to ``"down"``.
        kb_id = self._resolve_knowledge_base_id()
        client = self._control_client()
        await self._run_sync(client.get_knowledge_base, knowledgeBaseId=kb_id)
        return True
