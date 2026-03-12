"""Authentication data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Represents an authenticated caller.

    Populated by the auth middleware and stored in a request-scoped
    contextvar.  Downstream code (routes, enforcement, runners) reads
    the principal to make authorization and scoping decisions.
    """

    id: str
    type: str  # "user" | "service" | "anonymous"
    groups: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_anonymous(self) -> bool:
        return self.type == "anonymous"

    @property
    def is_admin(self) -> bool:
        return "admin" in self.scopes


ANONYMOUS_PRINCIPAL = AuthenticatedPrincipal(
    id="anonymous",
    type="anonymous",
)

# Used by NoopAuthBackend — dev mode gets full access, not "anonymous".
NOOP_PRINCIPAL = AuthenticatedPrincipal(
    id="noop",
    type="user",
    scopes=frozenset({"admin"}),
)
