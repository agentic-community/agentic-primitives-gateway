"""API key authentication backend."""

from __future__ import annotations

import logging

from starlette.requests import Request

from agentic_primitives_gateway.auth.base import AuthBackend
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal

logger = logging.getLogger(__name__)


class ApiKeyAuthBackend(AuthBackend):
    """Authenticate requests via static API keys.

    Keys are configured in the server YAML and mapped to principals::

        auth:
          backend: api_key
          api_keys:
            - key: "sk-dev-12345"
              principal_id: "dev-user"
              principal_type: "user"
              groups: ["engineering"]
              scopes: ["admin"]

    The key is read from the ``Authorization`` header (``Bearer <key>``)
    or the ``X-Api-Key`` header.
    """

    def __init__(self, api_keys: list[dict[str, object]]) -> None:
        self._keys: dict[str, AuthenticatedPrincipal] = {}
        for entry in api_keys:
            key = str(entry["key"])
            raw_groups = entry.get("groups") or []
            raw_scopes = entry.get("scopes") or []
            assert isinstance(raw_groups, list)
            assert isinstance(raw_scopes, list)
            self._keys[key] = AuthenticatedPrincipal(
                id=str(entry.get("principal_id", key)),
                type=str(entry.get("principal_type", "user")),
                groups=frozenset(str(g) for g in raw_groups),
                scopes=frozenset(str(s) for s in raw_scopes),
            )
        logger.info("ApiKeyAuthBackend initialized with %d keys", len(self._keys))

    def _extract_key(self, request: Request) -> str | None:
        """Extract the API key from request headers."""
        # Try Authorization: Bearer <key>
        auth_header = request.headers.get("authorization")
        if auth_header:
            parts = auth_header.split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return parts[1].strip()

        # Try X-Api-Key header
        return request.headers.get("x-api-key")

    async def authenticate(self, request: Request) -> AuthenticatedPrincipal | None:
        key = self._extract_key(request)
        if key is None:
            return None
        principal = self._keys.get(key)
        if principal is None:
            return None
        return principal
