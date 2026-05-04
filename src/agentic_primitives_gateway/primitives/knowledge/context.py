"""Per-request knowledge-scoping context.

Knowledge retrieval targets a bulk-indexed corpus (support KB, product
docs, code repository).  The namespace is **agent-scoped**, not
user-scoped by default — every user chatting with the same agent hits
the same corpus.  Deployments that need per-user corpora can include
``{principal_id}`` in the template (see
``agents/namespace.py::resolve_knowledge_namespace``).

Handlers read the namespace via ``get_knowledge_namespace()`` instead
of receiving it as a param, matching the per-primitive context pattern
used by memory / browser / code_interpreter.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_knowledge_namespace: ContextVar[str | None] = ContextVar("apg_knowledge_namespace", default=None)

# ``inline_citations`` on an agent spec enables ``[N]`` markers in
# knowledge_search tool output.  The runner sets this contextvar at run
# start so handlers can adjust their LLM-facing format; the UI reads the
# markers on the streamed tokens and renders them as pills.
_knowledge_inline_citations: ContextVar[bool] = ContextVar("apg_knowledge_inline_citations", default=False)


# Per-run counter so citation markers are globally unique across multiple
# knowledge_search calls in the same turn.  Each call increments and
# claims a range (``[base]``..``[base+len(chunks)-1]``) so the UI can map
# ``[N]`` back to a specific chunk regardless of which call produced it.
_knowledge_citation_counter: ContextVar[int] = ContextVar("apg_knowledge_citation_counter", default=0)


def set_knowledge_namespace(namespace: str | None) -> Token:
    """Set the knowledge namespace for this request."""
    return _knowledge_namespace.set(namespace)


def get_knowledge_namespace() -> str | None:
    """Read the knowledge namespace for this request."""
    return _knowledge_namespace.get()


def reset_knowledge_namespace(token: Token) -> None:
    """Restore the knowledge namespace to what it was before ``set_knowledge_namespace``."""
    _knowledge_namespace.reset(token)


def set_knowledge_inline_citations(enabled: bool) -> Token:
    return _knowledge_inline_citations.set(enabled)


def get_knowledge_inline_citations() -> bool:
    return _knowledge_inline_citations.get()


def reset_knowledge_inline_citations(token: Token) -> None:
    _knowledge_inline_citations.reset(token)


def claim_citation_indices(count: int) -> int:
    """Reserve ``count`` citation indices and return the starting index.

    The counter is per-run (reset by the runner's ``_init_context``) so
    ``[0]``, ``[1]``, ... are globally unique across all
    ``knowledge_search`` calls in a turn.  Callers emit ``[base]``
    through ``[base + count - 1]`` as markers and the UI renders them
    as pills keyed on the global index.
    """
    base = _knowledge_citation_counter.get()
    _knowledge_citation_counter.set(base + count)
    return base


def reset_citation_counter() -> Token:
    """Start a fresh range of citation indices for a new run."""
    return _knowledge_citation_counter.set(0)


def restore_citation_counter(token: Token) -> None:
    _knowledge_citation_counter.reset(token)
