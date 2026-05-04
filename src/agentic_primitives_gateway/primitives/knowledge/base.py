from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentic_primitives_gateway.models.knowledge import (
    DocumentInfo,
    IngestDocument,
    IngestResult,
    QueryResponse,
    RetrievedChunk,
)


class KnowledgeProvider(ABC):
    """Abstract base class for knowledge / RAG providers.

    Knowledge providers unify document-centric retrieval — vector search,
    property-graph traversal, and hybrid GraphRAG — behind one ABC.  They
    are distinct from ``MemoryProvider``: memory is user-scoped
    interaction state written during runs; knowledge is a bulk-ingested
    corpus queried for context.

    ``query()`` is optional.  The canonical pattern in this gateway is
    ``retrieve`` through knowledge, then synthesize through the LLM
    primitive — that keeps credentials, audit, and token accounting
    uniform.  Backends with native retrieve-and-generate (AgentCore KB,
    LlamaIndex QueryEngine) may override ``query`` as a convenience.

    The ABC auto-wraps ``retrieve``, ``query``, ``ingest``, and
    ``delete`` on every subclass via ``__init_subclass__`` to emit
    primitive-specific audit events and Prometheus metrics.  Subclasses
    do not emit these themselves — the enrichment is inherited.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Local import — avoids a top-level cycle between providers and
        # the audit subsystem, which is imported later than primitives.
        from agentic_primitives_gateway.primitives.knowledge._audit import (
            wrap_delete,
            wrap_ingest,
            wrap_query,
            wrap_retrieve,
        )

        own = cls.__dict__
        if "retrieve" in own:
            cls.retrieve = wrap_retrieve(own["retrieve"])  # type: ignore[method-assign]
        if "query" in own:
            cls.query = wrap_query(own["query"])  # type: ignore[method-assign]
        if "ingest" in own:
            cls.ingest = wrap_ingest(own["ingest"])  # type: ignore[method-assign]
        if "delete" in own:
            cls.delete = wrap_delete(own["delete"])  # type: ignore[method-assign]

    @abstractmethod
    async def ingest(
        self,
        namespace: str,
        documents: list[IngestDocument],
    ) -> IngestResult: ...

    @abstractmethod
    async def retrieve(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        *,
        include_citations: bool = False,
    ) -> list[RetrievedChunk]:
        """Retrieve chunks for ``query`` within ``namespace``.

        When ``include_citations`` is ``True``, providers that can produce
        structured source references populate ``RetrievedChunk.citations``
        (page, URI, span, etc.).  Providers without citation support
        leave the field ``None``; callers must tolerate that.  The flag
        is opt-in so the default retrieve path stays lightweight.
        """
        ...

    @abstractmethod
    async def delete(self, namespace: str, document_id: str) -> bool: ...

    @abstractmethod
    async def list_documents(
        self,
        namespace: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentInfo]: ...

    # ── Optional retrieve-and-generate ─────────────────────────────────

    async def query(
        self,
        namespace: str,
        question: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> QueryResponse:
        """Native retrieve-and-generate.  Optional; default raises.

        Backends that implement this bypass ``registry.llm`` unless they
        explicitly route synthesis through the LLM primitive — document
        the choice in the backend's docstring so operators know whether
        per-request credential and audit wiring still applies.
        """
        raise NotImplementedError

    # ── Discovery / health ─────────────────────────────────────────────

    async def list_namespaces(self) -> list[str]:
        """List all known knowledge namespaces.

        Providers that cannot enumerate namespaces should return ``[]``.
        """
        return []

    async def healthcheck(self) -> bool | str:
        return True
