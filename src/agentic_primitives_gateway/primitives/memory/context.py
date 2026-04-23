"""Per-request memory-scoping context.

Memory operations happen in one of three namespace scopes, each with
distinct semantics:

- **User-scoped memory** (``memory_namespace``) — agent-and-user-private
  (e.g. Alice's conversation history with a shared ``researcher`` agent
  is isolated from Bob's).  Set by the runner at run start from the
  agent spec template via ``resolve_memory_namespace()``.

- **Team-scoped shared memory** (``shared_memory_namespace``) — a single
  namespace visible to all workers in a team run.  Set by the team
  runner before each worker invocation.

- **Agent-level shared pools** (``memory_pools``) — a named dict of
  cross-user namespaces an agent's ``shared_namespaces`` declaration
  opens up.  Tools (``share_to``, ``read_from_pool``, …) take a
  ``pool`` argument naming the entry.

Handlers read these via ``get_*`` accessors instead of receiving them
as params, so ``build_tool_list`` / ``_bind_handler`` don't need a
per-primitive dispatch table.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_memory_namespace: ContextVar[str | None] = ContextVar("apg_memory_namespace", default=None)
_shared_memory_namespace: ContextVar[str | None] = ContextVar("apg_shared_memory_namespace", default=None)
_memory_pools: ContextVar[dict[str, str] | None] = ContextVar("apg_memory_pools", default=None)


# ── Per-user memory namespace ────────────────────────────────────────


def set_memory_namespace(namespace: str | None) -> Token:
    """Set the user-scoped memory namespace for this request."""
    return _memory_namespace.set(namespace)


def get_memory_namespace() -> str | None:
    """Read the user-scoped memory namespace for this request."""
    return _memory_namespace.get()


def reset_memory_namespace(token: Token) -> None:
    """Restore the memory namespace to what it was before ``set_memory_namespace``."""
    _memory_namespace.reset(token)


# ── Team-scoped shared namespace (one per team run) ──────────────────


def set_shared_memory_namespace(namespace: str | None) -> Token:
    return _shared_memory_namespace.set(namespace)


def get_shared_memory_namespace() -> str | None:
    return _shared_memory_namespace.get()


def reset_shared_memory_namespace(token: Token) -> None:
    _shared_memory_namespace.reset(token)


# ── Agent-level shared pools (many, keyed by declared pool name) ─────


def set_memory_pools(pools: dict[str, str] | None) -> Token:
    return _memory_pools.set(pools)


def get_memory_pools() -> dict[str, str] | None:
    return _memory_pools.get()


def reset_memory_pools(token: Token) -> None:
    _memory_pools.reset(token)
