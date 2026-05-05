"""Intent test: ``KnowledgeProvider.__init_subclass__`` auto-wraps every
subclass for audit + metrics — backends don't emit these themselves.

This guards the contract advertised in ``primitives/knowledge/base.py``:

    The ABC auto-wraps ``retrieve``, ``query``, ``ingest``, and ``delete``
    on every subclass via ``__init_subclass__`` to emit primitive-specific
    audit events and Prometheus metrics.  Subclasses do not emit these
    themselves — the enrichment is inherited.

If someone refactors ``__init_subclass__`` and breaks the auto-wrap, the
existing per-backend tests still pass (their implementations never emit
the events) — only this test fails.  That's the whole point.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.models.knowledge import (
    DocumentInfo,
    IngestDocument,
    IngestResult,
    QueryResponse,
    RetrievedChunk,
)
from agentic_primitives_gateway.primitives.knowledge.base import KnowledgeProvider


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
async def audit_sink() -> AsyncIterator[_CollectorSink]:
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)
    try:
        yield sink
    finally:
        await router.shutdown(timeout=1.0)
        set_audit_router(None)


class ProbeKnowledgeProvider(KnowledgeProvider):
    """Throwaway backend defined purely inside this test.

    It does the minimum each method must do.  It **never** emits audit
    events or touches metrics — if those show up, the ABC wrap produced
    them, which is exactly the contract under test.
    """

    store_type = "probe"

    async def ingest(
        self,
        namespace: str,
        documents: list[IngestDocument],
    ) -> IngestResult:
        ids = [f"doc-{i}" for i in range(len(documents))]
        return IngestResult(document_ids=ids, ingested=len(documents))

    async def retrieve(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        return [
            RetrievedChunk(
                chunk_id="c1",
                document_id="d1",
                text="hit",
                score=0.87,
                metadata={},
            )
        ]

    async def query(
        self,
        namespace: str,
        question: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> Any:
        # The wrapper duck-types on ``.chunks`` / ``.usage``, so we use a
        # SimpleNamespace to attach a ``usage`` field the audit wrap can
        # read.  QueryResponse itself doesn't carry usage today; backends
        # that surface it do so via extra attributes on whatever object
        # they return — the wrapper must tolerate that.
        from types import SimpleNamespace

        return SimpleNamespace(
            answer="synthesized",
            chunks=[
                RetrievedChunk(
                    chunk_id="c1",
                    document_id="d1",
                    text="src",
                    score=0.5,
                    metadata={},
                )
            ],
            usage={"prompt_tokens": 12, "completion_tokens": 8},
        )

    async def delete(self, namespace: str, document_id: str) -> bool:
        return True

    async def list_documents(
        self,
        namespace: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentInfo]:
        return []


class FailingProbeKnowledgeProvider(KnowledgeProvider):
    store_type = "probe"

    async def ingest(
        self,
        namespace: str,
        documents: list[IngestDocument],
    ) -> IngestResult:
        raise RuntimeError("ingest boom")

    async def retrieve(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        raise RuntimeError("retrieve boom")

    async def query(
        self,
        namespace: str,
        question: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> QueryResponse:
        raise RuntimeError("query boom")

    async def delete(self, namespace: str, document_id: str) -> bool:
        raise RuntimeError("delete boom")

    async def list_documents(
        self,
        namespace: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentInfo]:
        return []


class TestAutoAuditWrap:
    """A fresh subclass emits every expected ``knowledge.*`` audit event."""

    async def test_retrieve_emits_audit_event_and_metric(self, audit_sink: _CollectorSink) -> None:
        provider = ProbeKnowledgeProvider()

        chunks_before = metrics.KNOWLEDGE_CHUNKS_RETRIEVED.labels(provider="probe", store_type="probe")._value.get()

        chunks = await provider.retrieve("ns-a", "q", top_k=4)
        await asyncio.sleep(0.02)

        assert len(chunks) == 1

        retrieve_events = [e for e in audit_sink.events if e.action == AuditAction.KNOWLEDGE_RETRIEVE]
        assert len(retrieve_events) == 1, "wrap must emit exactly one knowledge.retrieve"
        event = retrieve_events[0]
        assert event.outcome == AuditOutcome.SUCCESS
        assert event.resource_id == "ns-a"
        assert event.metadata["provider"] == "probe"
        assert event.metadata["store_type"] == "probe"
        assert event.metadata["chunk_count"] == 1
        assert event.metadata["top_k"] == 4
        assert event.metadata["top_score"] == pytest.approx(0.87, abs=1e-4)

        chunks_after = metrics.KNOWLEDGE_CHUNKS_RETRIEVED.labels(provider="probe", store_type="probe")._value.get()
        assert chunks_after == chunks_before + 1

    async def test_ingest_emits_audit_event_and_metric(self, audit_sink: _CollectorSink) -> None:
        provider = ProbeKnowledgeProvider()

        ingested_before = metrics.KNOWLEDGE_DOCUMENTS_INGESTED.labels(provider="probe", store_type="probe")._value.get()

        await provider.ingest("ns-b", [IngestDocument(text="hi"), IngestDocument(text="bye")])
        await asyncio.sleep(0.02)

        ingest_events = [e for e in audit_sink.events if e.action == AuditAction.KNOWLEDGE_INGEST]
        assert len(ingest_events) == 1
        event = ingest_events[0]
        assert event.outcome == AuditOutcome.SUCCESS
        assert event.resource_id == "ns-b"
        assert event.metadata["document_count"] == 2
        assert event.metadata["ingested"] == 2

        ingested_after = metrics.KNOWLEDGE_DOCUMENTS_INGESTED.labels(provider="probe", store_type="probe")._value.get()
        assert ingested_after == ingested_before + 2

    async def test_query_emits_audit_event_with_token_counts(self, audit_sink: _CollectorSink) -> None:
        provider = ProbeKnowledgeProvider()

        prompt_before = metrics.KNOWLEDGE_QUERY_TOKENS.labels(provider="probe", kind="prompt")._value.get()
        completion_before = metrics.KNOWLEDGE_QUERY_TOKENS.labels(provider="probe", kind="completion")._value.get()

        await provider.query("ns-c", "what?", top_k=3)
        await asyncio.sleep(0.02)

        query_events = [e for e in audit_sink.events if e.action == AuditAction.KNOWLEDGE_QUERY]
        assert len(query_events) == 1
        event = query_events[0]
        assert event.outcome == AuditOutcome.SUCCESS
        assert event.resource_id == "ns-c"
        assert event.metadata["prompt_tokens"] == 12
        assert event.metadata["completion_tokens"] == 8
        assert event.metadata["chunk_count"] == 1

        assert metrics.KNOWLEDGE_QUERY_TOKENS.labels(provider="probe", kind="prompt")._value.get() == prompt_before + 12
        assert (
            metrics.KNOWLEDGE_QUERY_TOKENS.labels(provider="probe", kind="completion")._value.get()
            == completion_before + 8
        )

    async def test_delete_emits_audit_event_and_metric(self, audit_sink: _CollectorSink) -> None:
        provider = ProbeKnowledgeProvider()

        deleted_before = metrics.KNOWLEDGE_DOCUMENTS_DELETED.labels(provider="probe", store_type="probe")._value.get()

        await provider.delete("ns-d", "doc-1")
        await asyncio.sleep(0.02)

        delete_events = [e for e in audit_sink.events if e.action == AuditAction.KNOWLEDGE_DELETE]
        assert len(delete_events) == 1
        event = delete_events[0]
        assert event.outcome == AuditOutcome.SUCCESS
        assert event.metadata["deleted"] is True
        assert event.metadata["document_id"] == "doc-1"

        deleted_after = metrics.KNOWLEDGE_DOCUMENTS_DELETED.labels(provider="probe", store_type="probe")._value.get()
        assert deleted_after == deleted_before + 1

    async def test_exceptions_emit_error_events(self, audit_sink: _CollectorSink) -> None:
        """Contract: failures must be auditable too — same action, outcome=ERROR."""
        provider = FailingProbeKnowledgeProvider()

        with pytest.raises(RuntimeError):
            await provider.ingest("ns-e", [IngestDocument(text="x")])
        with pytest.raises(RuntimeError):
            await provider.retrieve("ns-e", "q")
        with pytest.raises(RuntimeError):
            await provider.query("ns-e", "q")
        with pytest.raises(RuntimeError):
            await provider.delete("ns-e", "doc")
        await asyncio.sleep(0.02)

        error_actions = {e.action for e in audit_sink.events if e.outcome == AuditOutcome.ERROR}
        assert error_actions == {
            AuditAction.KNOWLEDGE_INGEST,
            AuditAction.KNOWLEDGE_RETRIEVE,
            AuditAction.KNOWLEDGE_QUERY,
            AuditAction.KNOWLEDGE_DELETE,
        }
        for e in audit_sink.events:
            if e.outcome == AuditOutcome.ERROR:
                assert e.metadata["error_type"] == "RuntimeError"

    async def test_query_not_implemented_does_not_emit(self, audit_sink: _CollectorSink) -> None:
        """``NotImplementedError`` from the default ``query`` is mapped to 501 by the
        route layer and must NOT produce a knowledge.query error event.
        """

        class NoQueryKnowledgeProvider(KnowledgeProvider):
            store_type = "probe"

            async def ingest(self, namespace: str, documents: list[IngestDocument]) -> IngestResult:
                return IngestResult(document_ids=[], ingested=0)

            async def retrieve(
                self,
                namespace: str,
                query: str,
                top_k: int = 10,
                filters: dict[str, Any] | None = None,
            ) -> list[RetrievedChunk]:
                return []

            async def delete(self, namespace: str, document_id: str) -> bool:
                return False

            async def list_documents(
                self,
                namespace: str,
                limit: int = 100,
                offset: int = 0,
            ) -> list[DocumentInfo]:
                return []

        provider = NoQueryKnowledgeProvider()
        with pytest.raises(NotImplementedError):
            await provider.query("ns", "q")
        await asyncio.sleep(0.02)

        # The default ``query`` wasn't *overridden* on the subclass, so the
        # wrap doesn't apply and nothing is emitted — that's the whole
        # point of ``if 'query' in own``.
        query_events = [e for e in audit_sink.events if e.action == AuditAction.KNOWLEDGE_QUERY]
        assert query_events == []
