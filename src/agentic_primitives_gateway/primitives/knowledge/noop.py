from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.models.knowledge import (
    DocumentInfo,
    IngestDocument,
    IngestResult,
    RetrievedChunk,
)
from agentic_primitives_gateway.primitives.knowledge.base import KnowledgeProvider

logger = logging.getLogger(__name__)


class NoopKnowledgeProvider(KnowledgeProvider):
    """No-op knowledge provider that logs calls but indexes nothing."""

    store_type = "noop"

    def __init__(self, **kwargs: Any) -> None:
        logger.info("NoopKnowledgeProvider initialized")

    async def ingest(
        self,
        namespace: str,
        documents: list[IngestDocument],
    ) -> IngestResult:
        logger.debug("noop knowledge ingest: ns=%s docs=%d", namespace, len(documents))
        document_ids = [doc.document_id or "" for doc in documents]
        return IngestResult(document_ids=document_ids, ingested=0)

    async def retrieve(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedChunk]:
        logger.debug("noop knowledge retrieve: ns=%s query=%s", namespace, query)
        return []

    async def delete(self, namespace: str, document_id: str) -> bool:
        logger.debug("noop knowledge delete: ns=%s doc=%s", namespace, document_id)
        return False

    async def list_documents(
        self,
        namespace: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DocumentInfo]:
        logger.debug("noop knowledge list_documents: ns=%s", namespace)
        return []
