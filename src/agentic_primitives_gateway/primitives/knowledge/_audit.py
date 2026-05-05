"""Knowledge-specific audit + metrics wrappers used by ``KnowledgeProvider.__init_subclass__``.

Every subclass gets ``retrieve`` / ``query`` / ``ingest`` / ``delete``
wrapped automatically to emit ``knowledge.retrieve`` / ``knowledge.query`` /
``knowledge.ingest`` / ``knowledge.delete`` audit events with chunk counts
and top-score metadata, and to increment the knowledge-specific Prometheus
metrics defined in ``metrics.py``.

This lives in a separate module so the ABC itself stays dependency-light:
the audit subsystem is imported lazily when subclasses are defined, not
when ``KnowledgeProvider`` itself is imported (it's imported early during
app bootstrap, same as other primitive ABCs).

Label conventions:
    - ``provider``: derived from the subclass name (``NoopKnowledgeProvider``
      → ``"noop"``).  Bounded by the set of installed subclasses.
    - ``store_type``: read from ``self.store_type`` if the backend sets
      it; falls back to ``"unknown"``.  Bounded by taxonomy:
      ``vector|graph|hybrid|native|noop|unknown``.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.primitives._metadata_scrub import apply_metadata_denylist, get_denylist


def _provider_label(instance: Any) -> str:
    name = type(instance).__name__
    if name.endswith("KnowledgeProvider"):
        name = name[: -len("KnowledgeProvider")]
    return name.lower() or "unknown"


def _store_type(instance: Any) -> str:
    value = getattr(instance, "store_type", None)
    return str(value) if value else "unknown"


def _emit(
    action: str,
    outcome: AuditOutcome,
    *,
    namespace: str,
    metadata: dict[str, Any],
) -> None:
    emit_audit_event(
        action=action,
        outcome=outcome,
        resource_type=ResourceType.KNOWLEDGE,
        resource_id=namespace or None,
        metadata=metadata,
    )


def _extract_chunk_metadata(chunk: Any) -> list[dict[str, Any]]:
    """Yield every ``metadata`` dict the denylist should touch on a chunk.

    Covers the chunk's own ``metadata`` plus each citation's
    ``metadata`` passthrough — otherwise operators could still leak via
    the Citation escape hatch.  Non-dict metadata (Pydantic defaults
    are always dict, but subclass surprises exist) and empty citation
    lists are naturally skipped.
    """
    out: list[dict[str, Any]] = []
    meta = getattr(chunk, "metadata", None)
    if isinstance(meta, dict):
        out.append(meta)
    for citation in getattr(chunk, "citations", None) or []:
        cmeta = getattr(citation, "metadata", None)
        if isinstance(cmeta, dict):
            out.append(cmeta)
    return out


def wrap_retrieve(func: Any) -> Any:
    """Wrap a coroutine ``retrieve`` with audit + metrics + metadata scrubbing.

    The wrapper is backward-compatible with subclasses that haven't yet
    adopted the ``include_citations`` kwarg: if the wrapped function
    doesn't declare it (detected via ``inspect.signature``), the flag is
    dropped on the call to ``func`` but still recorded in the audit
    event metadata.  Every in-tree provider declares it; the fallback
    exists so out-of-tree providers aren't force-broken by the ABC
    change.
    """
    try:
        func_sig = inspect.signature(func)
        func_accepts_include_citations = "include_citations" in func_sig.parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in func_sig.parameters.values()
        )
    except (TypeError, ValueError):
        func_accepts_include_citations = False

    @functools.wraps(func)
    async def wrapper(
        self: Any,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        *,
        include_citations: bool = False,
    ) -> Any:
        provider = _provider_label(self)
        store_type = _store_type(self)
        try:
            if func_accepts_include_citations:
                chunks = await func(self, namespace, query, top_k, filters, include_citations=include_citations)
            else:
                chunks = await func(self, namespace, query, top_k, filters)
        except Exception as exc:
            _emit(
                AuditAction.KNOWLEDGE_RETRIEVE,
                AuditOutcome.ERROR,
                namespace=namespace,
                metadata={
                    "provider": provider,
                    "store_type": store_type,
                    "top_k": top_k,
                    "error_type": type(exc).__name__,
                },
            )
            raise

        # Apply the operator-configured metadata denylist uniformly before
        # any downstream consumer (REST response, agent tool, audit
        # metadata) sees it.  Same scrubbing for every caller.
        apply_metadata_denylist(chunks, get_denylist("knowledge"), extract=_extract_chunk_metadata)

        chunk_count = len(chunks) if chunks is not None else 0
        top_score = 0.0
        if chunk_count:
            try:
                top_score = float(getattr(chunks[0], "score", 0.0) or 0.0)
            except (TypeError, ValueError):
                top_score = 0.0

        if chunk_count:
            metrics.KNOWLEDGE_CHUNKS_RETRIEVED.labels(provider=provider, store_type=store_type).inc(chunk_count)
            metrics.KNOWLEDGE_RETRIEVAL_SCORE.labels(provider=provider, store_type=store_type).observe(top_score)

        _emit(
            AuditAction.KNOWLEDGE_RETRIEVE,
            AuditOutcome.SUCCESS,
            namespace=namespace,
            metadata={
                "provider": provider,
                "store_type": store_type,
                "top_k": top_k,
                "chunk_count": chunk_count,
                "top_score": round(top_score, 4),
                "include_citations": include_citations,
            },
        )
        return chunks

    return wrapper


def wrap_query(func: Any) -> Any:
    """Wrap a coroutine ``query`` with audit + metrics."""

    @functools.wraps(func)
    async def wrapper(
        self: Any,
        namespace: str,
        question: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> Any:
        provider = _provider_label(self)
        store_type = _store_type(self)
        try:
            response = await func(self, namespace, question, top_k, filters)
        except NotImplementedError:
            # Route layer converts this to 501; don't audit as a failure.
            raise
        except Exception as exc:
            _emit(
                AuditAction.KNOWLEDGE_QUERY,
                AuditOutcome.ERROR,
                namespace=namespace,
                metadata={
                    "provider": provider,
                    "store_type": store_type,
                    "top_k": top_k,
                    "error_type": type(exc).__name__,
                },
            )
            raise

        chunks = getattr(response, "chunks", []) or []
        chunk_count = len(chunks)

        # Pull token usage if the backend surfaced it (Pydantic attrs or
        # an opaque ``usage`` dict — tolerate both).
        usage = getattr(response, "usage", None) or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0) if isinstance(usage, dict) else 0
        completion_tokens = int(usage.get("completion_tokens") or 0) if isinstance(usage, dict) else 0
        if prompt_tokens:
            metrics.KNOWLEDGE_QUERY_TOKENS.labels(provider=provider, kind="prompt").inc(prompt_tokens)
        if completion_tokens:
            metrics.KNOWLEDGE_QUERY_TOKENS.labels(provider=provider, kind="completion").inc(completion_tokens)

        _emit(
            AuditAction.KNOWLEDGE_QUERY,
            AuditOutcome.SUCCESS,
            namespace=namespace,
            metadata={
                "provider": provider,
                "store_type": store_type,
                "top_k": top_k,
                "chunk_count": chunk_count,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
        )
        return response

    return wrapper


def wrap_ingest(func: Any) -> Any:
    """Wrap a coroutine ``ingest`` with audit + metrics."""

    @functools.wraps(func)
    async def wrapper(
        self: Any,
        namespace: str,
        documents: list[Any],
    ) -> Any:
        provider = _provider_label(self)
        store_type = _store_type(self)
        try:
            result = await func(self, namespace, documents)
        except Exception as exc:
            _emit(
                AuditAction.KNOWLEDGE_INGEST,
                AuditOutcome.ERROR,
                namespace=namespace,
                metadata={
                    "provider": provider,
                    "store_type": store_type,
                    "document_count": len(documents) if documents is not None else 0,
                    "error_type": type(exc).__name__,
                },
            )
            raise

        ingested = int(getattr(result, "ingested", 0) or 0)
        if ingested:
            metrics.KNOWLEDGE_DOCUMENTS_INGESTED.labels(provider=provider, store_type=store_type).inc(ingested)

        _emit(
            AuditAction.KNOWLEDGE_INGEST,
            AuditOutcome.SUCCESS,
            namespace=namespace,
            metadata={
                "provider": provider,
                "store_type": store_type,
                "document_count": len(documents) if documents is not None else 0,
                "ingested": ingested,
            },
        )
        return result

    return wrapper


def wrap_delete(func: Any) -> Any:
    """Wrap a coroutine ``delete`` with audit + metrics.

    Emits ``knowledge.delete`` at the provider boundary so programmatic
    callers (agent tools, background workers) are covered, not just the
    REST route.  Route handlers already emit via ``audit_mutation`` —
    the double event is intentional and matches ``ingest``.
    """

    @functools.wraps(func)
    async def wrapper(
        self: Any,
        namespace: str,
        document_id: str,
    ) -> Any:
        provider = _provider_label(self)
        store_type = _store_type(self)
        try:
            deleted = await func(self, namespace, document_id)
        except Exception as exc:
            _emit(
                AuditAction.KNOWLEDGE_DELETE,
                AuditOutcome.ERROR,
                namespace=namespace,
                metadata={
                    "provider": provider,
                    "store_type": store_type,
                    "document_id": document_id,
                    "error_type": type(exc).__name__,
                },
            )
            raise

        removed = 1 if deleted else 0
        if removed:
            metrics.KNOWLEDGE_DOCUMENTS_DELETED.labels(provider=provider, store_type=store_type).inc(removed)

        _emit(
            AuditAction.KNOWLEDGE_DELETE,
            AuditOutcome.SUCCESS,
            namespace=namespace,
            metadata={
                "provider": provider,
                "store_type": store_type,
                "document_id": document_id,
                "deleted": bool(deleted),
            },
        )
        return deleted

    return wrapper
