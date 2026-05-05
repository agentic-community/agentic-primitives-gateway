"""Intent test: a non-admin caller cannot read or write a knowledge
namespace scoped to a different user via the ``:u:{user_id}`` marker.

This is the security contract exposed on this branch:

  * ``routes/knowledge.py`` calls ``require_user_scoped(namespace, principal)``
    on every endpoint.  Namespaces containing ``:u:<other>`` must 403.
  * ``GET /api/v1/knowledge/namespaces`` filters non-admin listings so
    callers only see their own user-scoped namespaces.

The existing TestUserScoping block in test_knowledge.py documents that
it *cannot* exercise this — the noop auth middleware overwrites the
principal.  This test installs a custom auth backend on the ASGI app
so the contract actually runs, per the shared-pool lesson (test the
user-visible promise end-to-end, not just the branches).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from agentic_primitives_gateway.auth.base import AuthBackend
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.main import app


class _FixedPrincipalAuth(AuthBackend):
    """Hands every request the same principal — used to simulate a
    real non-admin user end-to-end.  No token parsing: the test sets
    who the "current user" is by reconfiguring the backend.
    """

    def __init__(self, principal: AuthenticatedPrincipal) -> None:
        self.principal = principal

    async def authenticate(self, request: Request) -> AuthenticatedPrincipal | None:
        return self.principal

    async def close(self) -> None:
        return None


def _client_as(principal: AuthenticatedPrincipal) -> Iterator[TestClient]:
    """Enter the TestClient lifespan FIRST (which installs a noop backend),
    then swap in our fixed-principal backend.  Restoring on teardown keeps
    other tests using the lifespan-installed default.
    """
    with TestClient(app) as client:
        previous = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = _FixedPrincipalAuth(principal)
        try:
            yield client
        finally:
            app.state.auth_backend = previous


@pytest.fixture
def alice_client() -> Iterator[TestClient]:
    """Client where the authenticated principal is a non-admin ``alice``."""
    yield from _client_as(
        AuthenticatedPrincipal(
            id="alice",
            type="user",
            groups=frozenset(),
            scopes=frozenset(),  # no admin
        )
    )


@pytest.fixture
def admin_client() -> Iterator[TestClient]:
    """Client where the authenticated principal is an admin."""
    yield from _client_as(
        AuthenticatedPrincipal(
            id="root",
            type="user",
            groups=frozenset(),
            scopes=frozenset({"admin"}),
        )
    )


class TestUserScopedNamespaceIsolation:
    """The intent: ``alice`` cannot read/write/list/delete/query against a
    namespace containing ``:u:bob`` — the user-scope marker belongs to bob.

    If the ``require_user_scoped`` check on any of the five endpoints is
    ever removed or bypassed, one of these assertions fails.  That is
    the whole reason this file exists — per-endpoint unit tests that
    don't run through real auth middleware can't catch removal of the
    check.
    """

    def test_retrieve_on_other_users_namespace_is_forbidden(self, alice_client: TestClient) -> None:
        resp = alice_client.post(
            "/api/v1/knowledge/project:u:bob/retrieve",
            json={"query": "anything"},
        )
        assert resp.status_code == 403

    def test_ingest_on_other_users_namespace_is_forbidden(self, alice_client: TestClient) -> None:
        resp = alice_client.post(
            "/api/v1/knowledge/project:u:bob/documents",
            json={"documents": [{"text": "smuggled"}]},
        )
        assert resp.status_code == 403

    def test_list_documents_on_other_users_namespace_is_forbidden(self, alice_client: TestClient) -> None:
        resp = alice_client.get("/api/v1/knowledge/project:u:bob/documents")
        assert resp.status_code == 403

    def test_delete_on_other_users_namespace_is_forbidden(self, alice_client: TestClient) -> None:
        resp = alice_client.delete("/api/v1/knowledge/project:u:bob/documents/any-doc")
        assert resp.status_code == 403

    def test_query_on_other_users_namespace_is_forbidden(self, alice_client: TestClient) -> None:
        resp = alice_client.post(
            "/api/v1/knowledge/project:u:bob/query",
            json={"question": "secrets?"},
        )
        assert resp.status_code == 403

    def test_own_user_scoped_namespace_is_allowed(self, alice_client: TestClient) -> None:
        """Sanity: alice CAN access her own ``:u:alice`` namespace."""
        resp = alice_client.post(
            "/api/v1/knowledge/project:u:alice/retrieve",
            json={"query": "anything"},
        )
        assert resp.status_code == 200

    def test_unscoped_namespace_is_allowed(self, alice_client: TestClient) -> None:
        """Sanity: a namespace with no ``:u:`` marker is shared/unscoped and passes through."""
        resp = alice_client.post(
            "/api/v1/knowledge/team-shared/retrieve",
            json={"query": "x"},
        )
        assert resp.status_code == 200

    def test_admin_bypasses_user_scope_check(self, admin_client: TestClient) -> None:
        """Admins must be able to access any user-scoped namespace (ops, support)."""
        resp = admin_client.post(
            "/api/v1/knowledge/project:u:bob/retrieve",
            json={"query": "anything"},
        )
        assert resp.status_code == 200


class _StubNamespaceKnowledgeProvider:
    """Minimal knowledge provider that returns a fixed list of namespaces.

    Only ``list_namespaces`` is exercised here; the route doesn't touch
    any other method, so we don't bother implementing the full ABC.

    The namespace data intentionally mirrors the convention used
    everywhere else in the gateway — ``:u:{user_id}`` is the TERMINAL
    segment, not a mid-string token.  ``require_user_scoped`` parses
    ``owner_id = value[idx + len(":u:"):]`` and compares it to
    ``principal.id``, so a namespace like ``"project:u:alice"`` parses
    cleanly to owner ``"alice"``.  A namespace like ``":u:alice:kb"``
    would parse to owner ``"alice:kb"`` and 403 alice from her own
    namespace — see :class:`TestRequireUserScopedFormatAssumption`.
    """

    store_type = "stub"

    async def list_namespaces(self) -> list[str]:
        return ["project:u:alice", "project:u:bob", "shared-corpus"]


@pytest.fixture
def stub_knowledge_provider() -> Iterator[None]:
    """Swap ``registry.knowledge`` for a stub that returns real namespace data.

    Without this, the test would run against the noop provider, which
    returns ``[]`` — meaning the filter under test could be
    ``return []`` and every assertion would still pass vacuously.  This
    is the exact failure mode the ``test_intent`` memo warns about.

    The stub replaces the ``MetricsProxy``-wrapped provider with a bare
    instance.  That's acceptable here because (a) the route under test
    doesn't depend on metrics instrumentation, and (b) the fixture
    restores the original ``MetricsProxy`` on teardown via try/finally.
    """
    from agentic_primitives_gateway.models.enums import Primitive
    from agentic_primitives_gateway.registry import registry

    knowledge = registry.get_primitive(Primitive.KNOWLEDGE)
    original = knowledge.get()
    stub = _StubNamespaceKnowledgeProvider()
    knowledge._providers[knowledge.default_name] = stub  # type: ignore[assignment]
    try:
        yield
    finally:
        knowledge._providers[knowledge.default_name] = original


class TestNamespaceListingFiltersByUserScope:
    """Intent: ``GET /namespaces`` shows admins everything, and shows non-admins
    only their own ``:u:{principal.id}`` entries.

    Unscoped (shared-corpus) namespaces are intentionally NOT shown
    to non-admins.  The REST surface for those namespaces currently
    has no per-principal ACL, so advertising them would point
    non-admins at resources they can't safely use.  End users reach
    shared corpora through the ``search_knowledge`` agent tool instead
    (gated by the agent's ``shared_with``).

    The shared-namespace REST access model is a known limitation —
    see the shared-namespace ACL follow-up issue.  When that lands,
    this listing behavior is expected to change.

    The stub here returns realistic data (one alice-scoped, one
    bob-scoped, one shared) so the assertions are non-vacuous: the
    filter could not be ``return []`` and pass these tests.
    """

    def test_non_admin_sees_only_own_scoped_entries(
        self,
        alice_client: TestClient,
        stub_knowledge_provider: None,
    ) -> None:
        resp = alice_client.get("/api/v1/knowledge/namespaces")
        assert resp.status_code == 200
        # Alice sees only her user-scoped entry — not bob's, not the
        # shared corpus (deliberately hidden from non-admin listings
        # under the current REST model).
        assert resp.json()["namespaces"] == ["project:u:alice"]

    def test_non_admin_cannot_see_other_users_scoped_entries(
        self,
        alice_client: TestClient,
        stub_knowledge_provider: None,
    ) -> None:
        """Explicit negative assertion: ``project:u:bob`` must NOT appear."""
        resp = alice_client.get("/api/v1/knowledge/namespaces")
        assert "project:u:bob" not in resp.json()["namespaces"]

    def test_non_admin_does_not_see_unscoped_shared_namespaces(
        self,
        alice_client: TestClient,
        stub_knowledge_provider: None,
    ) -> None:
        """The shared-corpus entry is intentionally hidden from non-admins.

        This matches the current REST-level guarantee: non-admins only
        see things they own.  Shared corpora are reached through the
        agent tool path.  Changing this test is the first signal that
        the shared-namespace ACL follow-up has landed.
        """
        resp = alice_client.get("/api/v1/knowledge/namespaces")
        assert "shared-corpus" not in resp.json()["namespaces"]

    def test_admin_sees_all_entries(
        self,
        admin_client: TestClient,
        stub_knowledge_provider: None,
    ) -> None:
        resp = admin_client.get("/api/v1/knowledge/namespaces")
        assert resp.status_code == 200
        assert set(resp.json()["namespaces"]) == {
            "project:u:alice",
            "project:u:bob",
            "shared-corpus",
        }


class TestRequireUserScopedFormatAssumption:
    """Intent: ``:u:{user_id}`` must be the TERMINAL segment of a namespace.

    ``require_user_scoped`` in ``routes/_helpers.py`` parses::

        owner_id = value[idx + len(":u:"):]

    It takes everything after the ``:u:`` marker as the user_id.  A
    well-formed namespace like ``"project:u:alice"`` parses to owner
    ``"alice"``.  A malformed namespace like ``":u:alice:kb"`` parses
    to owner ``"alice:kb"``, which does NOT match principal ``"alice"``
    — so alice would be 403'd from her own namespace.

    This test pins the assumption so that (a) anyone who "improves"
    the parser to return the middle segment sees this fail, and (b)
    anyone who mis-constructs a namespace (e.g. ``:u:{id}:suffix``)
    discovers the incompatibility immediately instead of mid-incident.
    """

    def test_trailing_u_user_segment_is_accepted(self, alice_client: TestClient) -> None:
        """Canonical form: ``<anything>:u:{user_id}`` — alice can access."""
        resp = alice_client.post(
            "/api/v1/knowledge/project:u:alice/retrieve",
            json={"query": "x"},
        )
        assert resp.status_code == 200

    def test_u_user_extra_segment_is_rejected_as_other_user(self, alice_client: TestClient) -> None:
        """Malformed: ``:u:alice:kb`` parses owner as ``"alice:kb"`` — not alice.

        This is the convention guard: if someone constructs a namespace
        with extra segments AFTER the user_id, the parser treats the
        full trailing string as the owner, and alice is 403'd from
        what she intended to be her own namespace.  That's the signal
        to stop and fix the namespace construction code — not a bug
        to "fix" in the parser.
        """
        resp = alice_client.post(
            "/api/v1/knowledge/:u:alice:kb/retrieve",
            json={"query": "x"},
        )
        assert resp.status_code == 403
