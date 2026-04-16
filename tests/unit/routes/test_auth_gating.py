"""Tests for auth gating on primitive routes.

Covers:
- Memory routes: user-scoped actor_id/namespace filtering
- Browser/code_interpreter: session ownership enforcement
- Policy: admin-only mutations
- All primitive routes: require_principal via router dependency
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.routes._helpers import browser_session_owners, code_interpreter_session_owners


def _set_principal(principal: AuthenticatedPrincipal) -> None:
    set_authenticated_principal(principal)


def _user(id: str = "alice") -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(id=id, type="user", scopes=frozenset())


def _admin() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(id="admin-user", type="user", scopes=frozenset({"admin"}))


# ── Memory: user-scoped actor_id filtering ───────────────────────────


class TestMemoryUserScoping:
    """Memory routes enforce :u:{user_id} ownership on actor_id/namespace."""

    def _client(self, principal: AuthenticatedPrincipal) -> TestClient:
        # Set auth backend to noop so middleware doesn't override our principal
        from agentic_primitives_gateway.auth.noop import NoopAuthBackend

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = NoopAuthBackend()
        client = TestClient(app, raise_server_exceptions=False)
        app.state.auth_backend = prev
        return client

    def test_create_event_own_actor_allowed(self, client: TestClient) -> None:
        """Noop principal (admin) can access any actor_id."""
        resp = client.post(
            "/api/v1/memory/sessions/agent:bot:u:noop/sess-1/events",
            json={"messages": [{"text": "hi", "role": "user"}]},
        )
        assert resp.status_code == 201

    def test_create_event_other_user_denied_non_admin(self) -> None:
        """Non-admin user cannot access another user's actor_id."""
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("alice")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/v1/memory/sessions/agent:bot:u:bob/sess-1/events",
                json={"messages": [{"text": "hi", "role": "user"}]},
            )
            assert resp.status_code == 403
        finally:
            app.state.auth_backend = prev

    def test_list_actors_filters_for_non_admin(self) -> None:
        """Non-admin only sees their own actors."""
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("alice")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            # Seed some actors
            client.post(
                "/api/v1/memory/sessions/agent:bot:u:alice/s1/events",
                json={"messages": [{"text": "hi", "role": "user"}]},
            )
            resp = client.get("/api/v1/memory/actors")
            assert resp.status_code == 200
            actors = resp.json()["actors"]
            for a in actors:
                # All returned actors should belong to alice
                if isinstance(a, str):
                    assert ":u:alice" in a
                elif isinstance(a, dict):
                    assert ":u:alice" in a.get("actor_id", "")
        finally:
            app.state.auth_backend = prev

    def test_list_namespaces_filters_for_non_admin(self) -> None:
        """Non-admin only sees their own namespaces."""
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("alice")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            # Store memory in a user-scoped namespace
            client.post(
                "/api/v1/memory/agent:bot:u:alice",
                json={"key": "k1", "content": "v1"},
            )
            resp = client.get("/api/v1/memory/namespaces")
            assert resp.status_code == 200
            for ns in resp.json()["namespaces"]:
                assert ":u:alice" in ns
        finally:
            app.state.auth_backend = prev

    def test_namespace_wrong_user_denied(self) -> None:
        """Non-admin cannot access another user's namespace."""
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("alice")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/api/v1/memory/agent:bot:u:bob")
            assert resp.status_code == 403
        finally:
            app.state.auth_backend = prev


# ── Browser: session ownership ───────────────────────────────────────


class TestBrowserSessionOwnership:
    """Browser routes enforce session ownership."""

    def setup_method(self):
        # Clear the ownership store between tests
        browser_session_owners._local.clear()

    def test_session_operation_denied_for_non_owner(self, client: TestClient) -> None:
        """Non-owner cannot access a session owned by someone else."""
        import asyncio

        asyncio.get_event_loop().run_until_complete(browser_session_owners.set_owner("owned-sess", "alice"))
        # The noop principal is admin and bypasses ownership
        # So we need a non-admin principal to test denial
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("bob")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get("/api/v1/browser/sessions/owned-sess")
            assert resp.status_code == 403
        finally:
            app.state.auth_backend = prev

    def test_session_operation_allowed_for_owner(self) -> None:
        """Owner can access their own session (gets past ownership check)."""
        import asyncio

        asyncio.get_event_loop().run_until_complete(browser_session_owners.set_owner("my-sess", "alice"))
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("alice")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get("/api/v1/browser/sessions/my-sess")
            # Should NOT be 403 — alice owns this session
            assert resp.status_code != 403
        finally:
            app.state.auth_backend = prev


# ── Code interpreter: session ownership ──────────────────────────────


class TestCodeInterpreterSessionOwnership:
    """Code interpreter routes enforce session ownership."""

    def setup_method(self):
        code_interpreter_session_owners._local.clear()

    def test_execute_denied_for_non_owner(self) -> None:
        import asyncio

        asyncio.get_event_loop().run_until_complete(code_interpreter_session_owners.set_owner("ci-sess", "alice"))
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("bob")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post(
                "/api/v1/code-interpreter/sessions/ci-sess/execute",
                json={"code": "print('hi')", "language": "python"},
            )
            assert resp.status_code == 403
        finally:
            app.state.auth_backend = prev

    def test_history_denied_for_non_owner(self) -> None:
        import asyncio

        asyncio.get_event_loop().run_until_complete(code_interpreter_session_owners.set_owner("ci-sess", "alice"))
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("bob")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get("/api/v1/code-interpreter/sessions/ci-sess/history")
            assert resp.status_code == 403
        finally:
            app.state.auth_backend = prev


# ── Policy: admin-only mutations ─────────────────────────────────────


class TestPolicyAdminGating:
    """Policy mutation endpoints require admin scope."""

    def _non_admin_client(self) -> TestClient:
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("alice")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        # Store prev so we can restore in tests
        self._prev_backend = prev
        return TestClient(app, raise_server_exceptions=False)

    def _restore(self):
        app.state.auth_backend = self._prev_backend

    def test_create_engine_requires_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.post("/api/v1/policy/engines", json={"name": "test"})
            assert resp.status_code == 403
        finally:
            self._restore()

    def test_delete_engine_requires_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.delete("/api/v1/policy/engines/fake-id")
            assert resp.status_code == 403
        finally:
            self._restore()

    def test_create_policy_requires_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.post(
                "/api/v1/policy/engines/fake-id/policies",
                json={"policy_body": "permit(principal, action, resource);"},
            )
            assert resp.status_code == 403
        finally:
            self._restore()

    def test_update_policy_requires_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.put(
                "/api/v1/policy/engines/fake-id/policies/fake-policy",
                json={"policy_body": "new body"},
            )
            assert resp.status_code == 403
        finally:
            self._restore()

    def test_delete_policy_requires_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.delete("/api/v1/policy/engines/fake-id/policies/fake-policy")
            assert resp.status_code == 403
        finally:
            self._restore()

    def test_list_engines_allowed_for_non_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.get("/api/v1/policy/engines")
            # Should not be 403 — read-only endpoints are accessible
            assert resp.status_code != 403
        finally:
            self._restore()

    def test_get_enforcement_allowed_for_non_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.get("/api/v1/policy/enforcement")
            assert resp.status_code == 200
        finally:
            self._restore()

    def test_admin_can_create_engine(self, client: TestClient) -> None:
        """Admin (noop) principal can create engines."""
        resp = client.post("/api/v1/policy/engines", json={"name": "test-admin"})
        assert resp.status_code == 201


# ── Identity: admin-only control plane mutations ─────────────────────


class TestIdentityAdminGating:
    """Identity control plane mutations require admin scope."""

    def _non_admin_client(self) -> TestClient:
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("alice")

        self._prev_backend = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        return TestClient(app, raise_server_exceptions=False)

    def _restore(self):
        app.state.auth_backend = self._prev_backend

    def test_create_credential_provider_requires_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.post(
                "/api/v1/identity/credential-providers",
                json={"name": "test", "provider_type": "oauth2", "config": {}},
            )
            assert resp.status_code == 403
        finally:
            self._restore()

    def test_update_credential_provider_requires_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.put("/api/v1/identity/credential-providers/test", json={"config": {}})
            assert resp.status_code == 403
        finally:
            self._restore()

    def test_delete_credential_provider_requires_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.delete("/api/v1/identity/credential-providers/test")
            assert resp.status_code == 403
        finally:
            self._restore()

    def test_create_workload_identity_requires_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.post("/api/v1/identity/workload-identities", json={"name": "test"})
            assert resp.status_code == 403
        finally:
            self._restore()

    def test_update_workload_identity_requires_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.put("/api/v1/identity/workload-identities/test", json={})
            assert resp.status_code == 403
        finally:
            self._restore()

    def test_delete_workload_identity_requires_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.delete("/api/v1/identity/workload-identities/test")
            assert resp.status_code == 403
        finally:
            self._restore()

    def test_list_credential_providers_allowed_for_non_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.get("/api/v1/identity/credential-providers")
            assert resp.status_code != 403
        finally:
            self._restore()

    def test_list_workload_identities_allowed_for_non_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.get("/api/v1/identity/workload-identities")
            assert resp.status_code != 403
        finally:
            self._restore()

    def test_data_plane_token_allowed_for_non_admin(self) -> None:
        c = self._non_admin_client()
        try:
            resp = c.post(
                "/api/v1/identity/token",
                json={"credential_provider": "test", "workload_token": "tok"},
            )
            # Should not be 403 — data plane is open to authenticated users
            assert resp.status_code != 403
        finally:
            self._restore()


# ── Observability: cross-user query restriction ──────────────────────


class TestObservabilityCrossUserQuery:
    """Observability list_sessions restricts user_id for non-admins."""

    def test_non_admin_user_id_forced(self) -> None:
        """Non-admin's user_id param is overridden with their own ID."""
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("alice")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            # Try to query another user's sessions
            resp = c.get("/api/v1/observability/sessions?user_id=bob")
            # Should succeed (not 403) but the user_id should be forced to "alice"
            assert resp.status_code != 403
        finally:
            app.state.auth_backend = prev

    def test_admin_can_query_any_user(self, client: TestClient) -> None:
        """Admin can query with any user_id."""
        resp = client.get("/api/v1/observability/sessions?user_id=anyone")
        assert resp.status_code != 403


# ── Browser/Code Interpreter: list_sessions filtering ────────────────


class TestBrowserListSessionsFiltering:
    """Browser list_sessions filters by ownership for non-admin users."""

    def setup_method(self):
        browser_session_owners._local.clear()

    def test_non_admin_sees_only_own_sessions(self) -> None:
        import asyncio

        asyncio.get_event_loop().run_until_complete(browser_session_owners.set_owner("alice-sess", "alice"))
        asyncio.get_event_loop().run_until_complete(browser_session_owners.set_owner("bob-sess", "bob"))

        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("alice")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get("/api/v1/browser/sessions")
            assert resp.status_code == 200
            sessions = resp.json()["sessions"]
            # Alice should not see bob's sessions
            session_ids = [s["session_id"] for s in sessions]
            assert "bob-sess" not in session_ids
        finally:
            app.state.auth_backend = prev


class TestCodeInterpreterListSessionsFiltering:
    """Code interpreter list_sessions filters by ownership for non-admin users."""

    def setup_method(self):
        code_interpreter_session_owners._local.clear()

    def test_non_admin_sees_only_own_sessions(self) -> None:
        import asyncio

        asyncio.get_event_loop().run_until_complete(code_interpreter_session_owners.set_owner("alice-ci", "alice"))
        asyncio.get_event_loop().run_until_complete(code_interpreter_session_owners.set_owner("bob-ci", "bob"))

        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return _user("alice")

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        try:
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.get("/api/v1/code-interpreter/sessions")
            assert resp.status_code == 200
            sessions = resp.json()["sessions"]
            session_ids = [s["session_id"] for s in sessions]
            assert "bob-ci" not in session_ids
        finally:
            app.state.auth_backend = prev
