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


def set_knowledge_namespace(namespace: str | None) -> Token:
    """Set the knowledge namespace for this request."""
    return _knowledge_namespace.set(namespace)


def get_knowledge_namespace() -> str | None:
    """Read the knowledge namespace for this request."""
    return _knowledge_namespace.get()


def reset_knowledge_namespace(token: Token) -> None:
    """Restore the knowledge namespace to what it was before ``set_knowledge_namespace``."""
    _knowledge_namespace.reset(token)
