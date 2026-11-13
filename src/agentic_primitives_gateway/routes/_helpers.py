"""Shared route helpers to reduce boilerplate."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from fastapi import HTTPException

from agentic_primitives_gateway.agents.namespace import pool_owner_segment
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

    User-scoped values contain ``:u:{encoded_user_id}`` where the
    complete percent-encoded ID is the terminal segment. Encode colons
    and percent signs inside IDs so ``bob`` and ``bob:admin`` are disjoint.

    Values with no ``:u:`` marker are shared and require a separate
    pool ACL from the calling route.
    """
    idx = value.find(_USER_SCOPE_SEP)
    if idx == -1:
        return  # not user-scoped — allow
    owner_id = value[idx + len(_USER_SCOPE_SEP) :]
    if owner_id != pool_owner_segment(principal.id) and not principal.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")


class SessionOwnershipStore:
    """Tracks each browser/code-interpreter session's authenticated owner.

    Redis is authoritative when configured. Released IDs are tombstoned so an
    in-flight authorized operation cannot resolve a replacement session.
    """

    _RELEASED = "\x00"

    def __init__(self) -> None:
        self._local: dict[str, str] = {}
        self._redis: Any | None = None

    def set_redis(self, redis_client: Any) -> None:
        self._redis = redis_client
        self._local.clear()  # Redis, not a stale local record, is authoritative.

    @staticmethod
    def _key(session_id: str) -> str:
        return f"session_owner:{session_id}"

    async def set_owner(self, session_id: str, owner_id: str) -> bool:
        """Claim an unowned ID; never replace an existing owner's session."""
        if self._redis is not None:
            return bool(await self._redis.set(self._key(session_id), owner_id, nx=True))
        if session_id in self._local:
            return False
        self._local[session_id] = owner_id
        return True

    async def get_owner(self, session_id: str) -> str | None:
        if self._redis is not None:
            owner = await self._redis.get(self._key(session_id))
            owner = owner.decode() if isinstance(owner, bytes) else owner
        else:
            owner = self._local.get(session_id)
        return None if owner == self._RELEASED else owner

    async def release_owner(self, session_id: str, owner_id: str) -> None:
        """Retire this claim without making its ID reusable by another user."""
        if self._redis is not None:
            await self._redis.eval(
                "if redis.call('GET', KEYS[1]) == ARGV[1] then "
                "return redis.call('SET', KEYS[1], ARGV[2]) else return 0 end",
                1,
                self._key(session_id),
                owner_id,
                self._RELEASED,
            )
        elif self._local.get(session_id) == owner_id:
            self._local[session_id] = self._RELEASED

    async def owned_session_ids(self, owner_id: str) -> set[str]:
        """Return only IDs currently owned by *owner_id* in the active store."""
        if self._redis is None:
            return {sid for sid, oid in self._local.items() if oid == owner_id}
        result: set[str] = set()
        async for key in self._redis.scan_iter(match="session_owner:*"):
            raw_key = key.decode() if isinstance(key, bytes) else str(key)
            oid = await self._redis.get(key)
            if isinstance(oid, bytes):
                oid = oid.decode()
            if oid == owner_id:
                result.add(raw_key.removeprefix("session_owner:"))
        return result

    async def require_owner(self, session_id: str, principal: AuthenticatedPrincipal) -> None:
        """Raise 403 unless the principal owns the session.

        Default-deny: if no owner is recorded (session created by a
        code path that forgot to claim it, or its ownership was revoked),
        a non-admin principal is denied. The previous default-allow behavior
        was a cross-tenant hazard: a session ID leak (screenshot URL,
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
