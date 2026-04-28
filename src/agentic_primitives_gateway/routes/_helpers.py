"""Shared route helpers to reduce boilerplate."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from fastapi import HTTPException

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import get_authenticated_principal

# Separator used by agents/namespace.py to embed user IDs in actor_ids
# and namespaces.  E.g. ``agent:bot:u:user-123``.
_USER_SCOPE_SEP = ":u:"


def require_principal() -> AuthenticatedPrincipal:
    """Return the authenticated principal. Raises if not set."""
    principal = get_authenticated_principal()
    if principal is None:
        raise RuntimeError("No authenticated principal — auth middleware did not run")
    return principal


def require_admin() -> AuthenticatedPrincipal:
    """Return the principal if it has admin scope. Raises 403 otherwise."""
    principal = require_principal()
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return principal


def require_user_scoped(value: str, principal: AuthenticatedPrincipal) -> None:
    """Raise 403 if *value* (actor_id or namespace) is user-scoped to a different user.

    User-scoped values contain ``:u:{user_id}`` where ``{user_id}`` MUST
    be the **terminal segment** — no trailing colons or further segments.
    This is a project-wide convention:

    * ``agent:bot:u:alice``            → owner=``alice``             ✓
    * ``agent:system:support:u:alice`` → owner=``alice``             ✓
    * ``:u:alice:kb``                  → owner=``alice:kb`` (wrong!) ✗

    The parser takes everything after the ``:u:`` marker as the owner
    id, so a value like ``:u:alice:kb`` parses to owner ``"alice:kb"``
    and fails the match against principal ``"alice"`` — alice would be
    403'd from what she thinks is her own namespace.  If you see that
    happen, the fix is to correct the *namespace construction code*
    to put ``:u:{user_id}`` at the end, not to relax this parser.

    Values that do NOT contain the ``:u:`` marker are considered
    unscoped (shared) and are allowed through — access control for
    unscoped namespaces is the responsibility of the calling route
    (e.g. admin-only surfaces, per-namespace ACLs).
    """
    idx = value.find(_USER_SCOPE_SEP)
    if idx == -1:
        return  # not user-scoped — allow
    owner_id = value[idx + len(_USER_SCOPE_SEP) :]
    if owner_id != principal.id and not principal.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")


class SessionOwnershipStore:
    """Tracks which principal created each browser/code-interpreter session.

    In-memory by default.  Call ``set_redis`` to enable cross-replica
    visibility via a shared Redis instance.
    """

    def __init__(self) -> None:
        self._local: dict[str, str] = {}
        self._redis: Any | None = None

    def set_redis(self, redis_client: Any) -> None:
        self._redis = redis_client

    @staticmethod
    def _key(session_id: str) -> str:
        return f"session_owner:{session_id}"

    async def set_owner(self, session_id: str, owner_id: str) -> None:
        self._local[session_id] = owner_id
        if self._redis:
            await self._redis.set(self._key(session_id), owner_id, ex=86400)

    async def get_owner(self, session_id: str) -> str | None:
        owner = self._local.get(session_id)
        if owner is None and self._redis:
            owner = await self._redis.get(self._key(session_id))
            if owner:
                self._local[session_id] = owner
        return owner

    async def delete(self, session_id: str) -> None:
        self._local.pop(session_id, None)
        if self._redis:
            await self._redis.delete(self._key(session_id))

    async def owned_session_ids(self, owner_id: str) -> set[str]:
        """Return the set of session IDs owned by *owner_id*.

        Checks the local cache first.  When Redis is configured, also
        scans ``session_owner:*`` keys to find sessions created on other
        replicas (cross-replica visibility).
        """
        result = {sid for sid, oid in self._local.items() if oid == owner_id}
        if self._redis:
            async for key in self._redis.scan_iter(match="session_owner:*"):
                k = str(key)
                sid = k.removeprefix("session_owner:")
                if sid not in result:
                    oid = await self._redis.get(k)
                    if oid == owner_id:
                        result.add(sid)
                        self._local[sid] = owner_id  # warm local cache
        return result

    async def require_owner(self, session_id: str, principal: AuthenticatedPrincipal) -> None:
        """Raise 403 unless the principal owns the session.

        Default-deny: if no owner is recorded (session created by a
        code path that forgot ``set_owner``, TTL expired, replica
        crashed before writing the owner record), a non-admin principal
        is denied.  The previous default-allow behavior was a
        cross-tenant hazard — a session ID leak (screenshot URL,
        observability trace, log) let any authenticated user drive
        another user's live browser / kernel session.
        """
        if principal.is_admin:
            return
        owner = await self.get_owner(session_id)
        if owner is None or owner != principal.id:
            raise HTTPException(status_code=403, detail="Forbidden")


# Shared stores — one per primitive.  Initialized at module import;
# ``main.py`` lifespan can call ``.set_redis()`` for multi-replica.
browser_session_owners = SessionOwnershipStore()
code_interpreter_session_owners = SessionOwnershipStore()


# ── Agent/Team spec addressing ────────────────────────────────────────
#
# Routes accept three forms of ``{name}``:
#
# 1. ``/agents/{name}`` (bare) — caller-context resolution via the
#    versioned store: ``(principal.id, name)`` first, else ``(system, name)``.
#    Never falls through to shared agents; qualified addressing is required
#    for those.
# 2. ``/agents/{owner_id}:{name}`` (qualified) — direct addressing.
# 3. ``/agents/{name}?owner=alice`` — admin-only alternative to qualified form.
#
# The helpers below parse + resolve these forms and apply ``require_access``.


def parse_spec_addr(
    raw_name: str,
    principal: AuthenticatedPrincipal,
    owner_query: str | None = None,
) -> tuple[str, str]:
    """Parse a route ``{name}`` parameter into ``(owner_id, bare_name)``.

    An empty ``owner_id`` return value means "caller-context resolution".
    Admin-only rules:

    * ``owner_query`` requires admin scope.
    * Qualified ``"{owner}:{name}"`` where ``owner != principal.id`` is
      allowed for non-admins but the share rules still apply when the
      resolved spec is loaded.
    """
    if owner_query:
        if not principal.is_admin:
            raise HTTPException(status_code=403, detail="?owner=... requires admin scope")
        return owner_query, raw_name
    if ":" in raw_name:
        owner, _, bare = raw_name.partition(":")
        if not owner or not bare:
            raise HTTPException(status_code=422, detail="Malformed qualified name")
        return owner, bare
    return "", raw_name  # bare — caller-context


async def resolve_agent_spec(
    store: Any,
    raw_name: str,
    principal: AuthenticatedPrincipal,
    *,
    owner_query: str | None = None,
) -> Any:
    """Resolve an ``AgentSpec`` by bare or qualified name + enforce access.

    Raises HTTPException 404 if nothing resolves, 403 if access check fails.
    """
    from agentic_primitives_gateway.auth.access import require_access

    owner, bare = parse_spec_addr(raw_name, principal, owner_query)
    if owner:
        spec = await store.resolve_qualified(owner, bare)
    else:
        spec = await store.resolve_for_caller(bare, principal)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Agent '{raw_name}' not found")
    # ``resolve_for_caller`` already applied check_access; qualified
    # lookups need the same check here.
    require_access(principal, spec.owner_id, spec.shared_with)
    return spec


async def resolve_team_spec(
    store: Any,
    raw_name: str,
    principal: AuthenticatedPrincipal,
    *,
    owner_query: str | None = None,
) -> Any:
    """Team equivalent of :func:`resolve_agent_spec`."""
    from agentic_primitives_gateway.auth.access import require_access

    owner, bare = parse_spec_addr(raw_name, principal, owner_query)
    if owner:
        spec = await store.resolve_qualified(owner, bare)
    else:
        spec = await store.resolve_for_caller(bare, principal)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{raw_name}' not found")
    require_access(principal, spec.owner_id, spec.shared_with)
    return spec


def handle_provider_errors(
    not_implemented: str = "Not supported by this provider",
    not_found: str | None = None,
) -> Callable[..., Any]:
    """Decorator that maps common provider exceptions to HTTP errors.

    - ``NotImplementedError`` → 501 with *not_implemented* message
    - ``KeyError`` → 404 with *not_found* message (only if *not_found* is set)

    Usage::

        @router.get("/something")
        @handle_provider_errors("Feature X not supported", not_found="Item not found")
        async def my_endpoint():
            return await registry.primitive.method()
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except NotImplementedError:
                raise HTTPException(status_code=501, detail=not_implemented) from None
            except KeyError:
                if not_found is not None:
                    raise HTTPException(status_code=404, detail=not_found) from None
                raise

        return wrapper

    return decorator
