"""LlamaIndex-backed knowledge provider.

One provider class, pluggable storage — same shape as ``Mem0MemoryProvider``.
``store_type`` picks vector / graph / hybrid; ``vector_store`` and
``graph_store`` config blocks are forwarded to LlamaIndex store factories.
Synthesis for ``query()`` routes through ``registry.llm`` via
``GatewayLlamaLLM`` so it inherits the gateway's provider routing,
credential resolution, audit, and token accounting.

Provider config example (in-memory dev default)::

    backend: agentic_primitives_gateway.primitives.knowledge.llamaindex.LlamaIndexKnowledgeProvider
    config:
      store_type: vector           # vector | graph | hybrid
      vector_store:                # optional — defaults to SimpleVectorStore (in-memory)
        provider: simple
      embed_model:                 # optional — defaults to OpenAI if OPENAI_API_KEY is set
        provider: bedrock
        config:
          model_name: amazon.titan-embed-text-v2:0
      llm:                         # optional — used ONLY by query()
        backend_name: bedrock      # pins a gateway LLM backend; falls
                                   # back to providers.llm.default when
                                   # unset (X-Provider-Llm contextvar is
                                   # intentionally bypassed — synthesis
                                   # is operator-scope, not caller-routable)
        model: us.anthropic.claude-sonnet-4-20250514-v1:0
        max_tokens: 2048
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

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


_NAMESPACE_METADATA_KEY = "_apg_namespace"
_SOURCE_METADATA_KEY = "_apg_source"


def _build_llamaindex_citations(node: Any, node_metadata: dict[str, Any], text: str) -> list[Citation]:
    """Build structured citations from a LlamaIndex node.

    LlamaIndex node metadata commonly carries ``page_label`` /
    ``page_number`` (from PDF readers) and ``file_path`` / ``file_name``
    (from file-based readers).  We surface the most useful fields into
    named Citation slots and dump the whole dict into ``metadata`` so
    provider-specific fields (element IDs, bounding boxes) still reach
    the UI.  Character offsets (``start_char_idx`` / ``end_char_idx``)
    are mapped to ``span`` when present — LlamaIndex populates these for
    many node-parser outputs.
    """
    source = (
        node_metadata.get(_SOURCE_METADATA_KEY)
        or node_metadata.get("source")
        or node_metadata.get("file_path")
        or node_metadata.get("file_name")
    )
    uri = node_metadata.get("url") or node_metadata.get("uri")
    page = node_metadata.get("page_label") or node_metadata.get("page_number")
    page_str = str(page) if page is not None else None

    start = getattr(node, "start_char_idx", None)
    end = getattr(node, "end_char_idx", None)
    span: tuple[int, int] | None = None
    if isinstance(start, int) and isinstance(end, int):
        span = (start, end)

    # Snippet is a short preview of the chunk text, primarily useful
    # when the UI wants to render a compact citation tile without the
    # full chunk body.  200 chars keeps it short; the full text stays on
    # the parent ``RetrievedChunk.text``.
    snippet = text[:200] if text else None

    passthrough = {
        k: v
        for k, v in node_metadata.items()
        if not k.startswith("_apg_")
        and k not in {"source", "file_path", "file_name", "url", "uri", "page_label", "page_number"}
    }

    return [
        Citation(
            source=str(source) if source else None,
            uri=str(uri) if uri else None,
            page=page_str,
            span=span,
            snippet=snippet,
            metadata=passthrough,
        )
    ]


def _require_llama_index() -> tuple[Any, Any, Any, Any, Any]:
    """Import LlamaIndex core lazily and return the symbols this module uses."""
    try:
        from llama_index.core import Document, PropertyGraphIndex, StorageContext, VectorStoreIndex
        from llama_index.core.vector_stores import MetadataFilter, MetadataFilters
    except ImportError as exc:  # pragma: no cover - covered by install-check tests
        raise ImportError(
            "LlamaIndex is not installed.  Install with "
            "`pip install 'agentic-primitives-gateway[knowledge-llamaindex]'`."
        ) from exc
    return Document, VectorStoreIndex, PropertyGraphIndex, StorageContext, (MetadataFilter, MetadataFilters)


class LlamaIndexKnowledgeProvider(SyncRunnerMixin, KnowledgeProvider):
    """Knowledge provider backed by LlamaIndex.

    Stores are swapped in via config (``vector_store`` / ``graph_store``),
    so one class handles dev-in-memory, FalkorDB graph, and managed vector
    stores.  Synthesis routes through ``registry.llm`` by default.
    """

    def __init__(
        self,
        *,
        store_type: str = "vector",
        vector_store: dict[str, Any] | None = None,
        graph_store: dict[str, Any] | None = None,
        embed_model: dict[str, Any] | None = None,
        llm: dict[str, Any] | None = None,
        storage_dir: str | None = None,
        **kwargs: Any,
    ) -> None:
        store_type = store_type.lower()
        if store_type not in ("vector", "graph", "hybrid"):
            raise ValueError(f"Unknown store_type '{store_type}'. Expected 'vector', 'graph', or 'hybrid'.")
        self.store_type = store_type
        self._vector_store_cfg = vector_store or {}
        self._graph_store_cfg = graph_store or {}
        self._embed_model_cfg = embed_model or {}
        self._llm_cfg = llm or {}
        self._storage_dir = storage_dir
        self._index: Any = None
        self._index_lock = threading.Lock()
        # Track doc-ids we've ingested per namespace so list/delete work
        # against storage layers that don't expose a native list API
        # (SimpleVectorStore is one such layer).
        self._doc_index: dict[str, dict[str, DocumentInfo]] = {}
        logger.info(
            "LlamaIndexKnowledgeProvider initialized (store_type=%s, vector=%s, graph=%s)",
            store_type,
            self._vector_store_cfg.get("provider", "simple") if self._vector_store_cfg else "simple",
            self._graph_store_cfg.get("provider") if self._graph_store_cfg else None,
        )

    # ── Index construction ─────────────────────────────────────────────

    def _get_index(self) -> Any:
        if self._index is None:
            with self._index_lock:
                if self._index is None:
                    self._index = self._build_index()
        return self._index

    def _build_index(self) -> Any:
        _Document, VectorStoreIndex, PropertyGraphIndex, StorageContext, _ = _require_llama_index()

        embed_model = self._build_embed_model()
        storage_kwargs: dict[str, Any] = {}

        if self.store_type in ("vector", "hybrid"):
            vector_store = self._build_vector_store()
            if vector_store is not None:
                storage_kwargs["vector_store"] = vector_store

        if self.store_type in ("graph", "hybrid"):
            graph_store = self._build_graph_store()
            if graph_store is not None:
                storage_kwargs["property_graph_store"] = graph_store

        storage_context = StorageContext.from_defaults(**storage_kwargs) if storage_kwargs else None

        if self.store_type == "graph":
            return PropertyGraphIndex.from_documents(
                [],
                storage_context=storage_context,
                embed_model=embed_model,
                show_progress=False,
            )
        return VectorStoreIndex.from_documents(
            [],
            storage_context=storage_context,
            embed_model=embed_model,
            show_progress=False,
        )

    def _build_embed_model(self) -> Any:
        if not self._embed_model_cfg:
            return None  # LlamaIndex default (OpenAI if OPENAI_API_KEY set, else error on first use)
        provider = self._embed_model_cfg.get("provider", "").lower()
        config = self._embed_model_cfg.get("config", {})
        if provider == "bedrock":
            from llama_index.embeddings.bedrock import BedrockEmbedding

            return BedrockEmbedding(**config)
        if provider == "openai":
            from llama_index.embeddings.openai import OpenAIEmbedding

            return OpenAIEmbedding(**config)
        if provider == "huggingface":
            from llama_index.embeddings.huggingface import HuggingFaceEmbedding

            return HuggingFaceEmbedding(**config)
        raise ValueError(f"Unknown embed_model.provider '{provider}'")

    def _build_vector_store(self) -> Any:
        provider = (self._vector_store_cfg.get("provider") or "simple").lower()
        config = self._vector_store_cfg.get("config", {})
        if provider == "simple":
            return None  # LlamaIndex default — SimpleVectorStore (in-memory)
        if provider == "pinecone":
            from llama_index.vector_stores.pinecone import PineconeVectorStore

            return PineconeVectorStore(**config)
        if provider == "pgvector":
            from llama_index.vector_stores.postgres import PGVectorStore

            return PGVectorStore.from_params(**config)
        if provider == "milvus":
            from llama_index.vector_stores.milvus import MilvusVectorStore

            return MilvusVectorStore(**config)
        if provider == "weaviate":
            from llama_index.vector_stores.weaviate import WeaviateVectorStore

            return WeaviateVectorStore(**config)
        raise ValueError(f"Unknown vector_store.provider '{provider}'")

    def _build_graph_store(self) -> Any:
        provider = (self._graph_store_cfg.get("provider") or "").lower()
        config = self._graph_store_cfg.get("config", {})
        if provider == "falkordb":
            from llama_index.graph_stores.falkordb import FalkorDBPropertyGraphStore

            return FalkorDBPropertyGraphStore(**config)
        if provider == "neo4j":
            from llama_index.graph_stores.neo4j import Neo4jPropertyGraphStore

            return Neo4jPropertyGraphStore(**config)
        raise ValueError(f"Unknown graph_store.provider '{provider}'")

    def _build_llm(self) -> Any:
        from agentic_primitives_gateway.primitives.knowledge._llama_llm_bridge import GatewayLlamaLLM

        return GatewayLlamaLLM(
            model=self._llm_cfg.get("model"),
            backend_name=self._llm_cfg.get("backend_name"),
            max_tokens=int(self._llm_cfg.get("max_tokens", 2048)),
            temperature=self._llm_cfg.get("temperature"),
        )

    # ── Primitive methods ──────────────────────────────────────────────

    async def ingest(
        self,
        namespace: str,
        documents: list[IngestDocument],
    ) -> IngestResult:
        Document, *_ = _require_llama_index()
        index = self._get_index()

        docs: list[Any] = []
        assigned_ids: list[str] = []
        for d in documents:
            doc_id = d.document_id or uuid.uuid4().hex
            metadata = dict(d.metadata or {})
            metadata[_NAMESPACE_METADATA_KEY] = namespace
            if d.source:
                metadata[_SOURCE_METADATA_KEY] = d.source
            docs.append(Document(text=d.text, metadata=metadata, id_=doc_id))
            assigned_ids.append(doc_id)

        def _insert() -> None:
            for doc in docs:
                index.insert(doc)

        await self._run_sync(_insert)

        ns_store = self._doc_index.setdefault(namespace, {})
        for doc_id, d in zip(assigned_ids, documents, strict=False):
            ns_store[doc_id] = DocumentInfo(
                document_id=doc_id,
                metadata=dict(d.metadata or {}),
                source=d.source,
            )

        return IngestResult(document_ids=assigned_ids, ingested=len(assigned_ids))

    async def retrieve(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        *,
        include_citations: bool = False,
    ) -> list[RetrievedChunk]:
        _, _, _, _, (MetadataFilter, MetadataFilters) = _require_llama_index()
        index = self._get_index()

        filter_list: list[Any] = [
            MetadataFilter(key=_NAMESPACE_METADATA_KEY, value=namespace),
        ]
        if filters:
            for key, value in filters.items():
                filter_list.append(MetadataFilter(key=key, value=value))
        metadata_filters = MetadataFilters(filters=filter_list)

        def _retrieve() -> list[Any]:
            retriever = index.as_retriever(
                similarity_top_k=top_k,
                filters=metadata_filters,
            )
            result: list[Any] = list(retriever.retrieve(query))
            return result

        nodes = await self._run_sync(_retrieve)

        chunks: list[RetrievedChunk] = []
        for node_with_score in nodes or []:
            node = getattr(node_with_score, "node", node_with_score)
            node_metadata = dict(getattr(node, "metadata", {}) or {})
            doc_id = node_metadata.get("ref_doc_id") or getattr(node, "ref_doc_id", None) or getattr(node, "id_", "")
            # Strip internal markers before handing metadata to the caller.
            clean_metadata = {k: v for k, v in node_metadata.items() if not k.startswith("_apg_")}
            if node_metadata.get(_SOURCE_METADATA_KEY):
                clean_metadata.setdefault("source", node_metadata[_SOURCE_METADATA_KEY])
            score = float(getattr(node_with_score, "score", 0.0) or 0.0)
            text = getattr(node, "text", "") or ""
            citations = _build_llamaindex_citations(node, node_metadata, text) if include_citations else None
            chunks.append(
                RetrievedChunk(
                    chunk_id=str(getattr(node, "id_", "")),
                    document_id=str(doc_id or ""),
                    text=text,
                    score=score,
                    metadata=clean_metadata,
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
        _, _, _, _, (MetadataFilter, MetadataFilters) = _require_llama_index()
        index = self._get_index()
        llm = self._build_llm()

        filter_list: list[Any] = [
            MetadataFilter(key=_NAMESPACE_METADATA_KEY, value=namespace),
        ]
        if filters:
            for key, value in filters.items():
                filter_list.append(MetadataFilter(key=key, value=value))
        metadata_filters = MetadataFilters(filters=filter_list)

        def _query() -> Any:
            engine = index.as_query_engine(
                llm=llm,
                similarity_top_k=top_k,
                filters=metadata_filters,
            )
            return engine.query(question)

        response = await self._run_sync(_query)

        chunks: list[RetrievedChunk] = []
        for node_with_score in getattr(response, "source_nodes", []) or []:
            node = getattr(node_with_score, "node", node_with_score)
            node_metadata = dict(getattr(node, "metadata", {}) or {})
            doc_id = node_metadata.get("ref_doc_id") or getattr(node, "ref_doc_id", None) or getattr(node, "id_", "")
            clean_metadata = {k: v for k, v in node_metadata.items() if not k.startswith("_apg_")}
            if node_metadata.get(_SOURCE_METADATA_KEY):
                clean_metadata.setdefault("source", node_metadata[_SOURCE_METADATA_KEY])
            chunks.append(
                RetrievedChunk(
                    chunk_id=str(getattr(node, "id_", "")),
                    document_id=str(doc_id or ""),
                    text=getattr(node, "text", "") or "",
                    score=float(getattr(node_with_score, "score", 0.0) or 0.0),
                    metadata=clean_metadata,
                )
            )

        return QueryResponse(answer=str(response), chunks=chunks)

    async def delete(self, namespace: str, document_id: str) -> bool:
        # Check the tracked doc index first — LlamaIndex's delete_ref_doc
        # silently no-ops on unknown IDs, so we can't use that as a
        # presence signal.
        ns_store = self._doc_index.get(namespace, {})
        if document_id not in ns_store:
            return False

        index = self._get_index()

        def _delete() -> bool:
            try:
                index.delete_ref_doc(document_id, delete_from_docstore=True)
                return True
            except Exception:
                logger.exception("LlamaIndex delete failed for %s/%s", namespace, document_id)
                return False

        removed = await self._run_sync(_delete)
        if removed:
            ns_store.pop(document_id, None)
        return bool(removed)

    async def list_documents(
        self,
        namespace: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentInfo]:
        # Track-side list: LlamaIndex's storage layers don't expose a
        # uniform "list documents by metadata filter" API — SimpleVectorStore
        # has no such API at all — so we maintain a per-namespace index
        # of what we've ingested through this provider instance.  Good
        # enough for dev/quickstart; production deployments can swap to
        # a backend with native listing (e.g. pgvector) and override.
        documents = list(self._doc_index.get(namespace, {}).values())
        return documents[offset : offset + limit]

    async def list_namespaces(self) -> list[str]:
        return sorted(self._doc_index.keys())

    async def healthcheck(self) -> bool:
        # Let exceptions propagate so the audit/metrics wrapper sees the
        # failure.  The route layer (``_check_provider_authenticated``)
        # already catches and maps to ``"down"`` for the dashboard.
        self._get_index()
        return True
