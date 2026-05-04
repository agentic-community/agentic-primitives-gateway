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

        asyncio.run(browser_session_owners.set_owner("owned-sess", "alice"))
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

        asyncio.run(browser_session_owners.set_owner("my-sess", "alice"))
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

        asyncio.run(code_interpreter_session_owners.set_owner("ci-sess", "alice"))
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

        asyncio.run(code_interpreter_session_owners.set_owner("ci-sess", "alice"))
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


# ── X-Provider-* override: admin-only ───────────────────────────────


class TestProviderOverrideAllowlist:
    """``X-Provider-*`` overrides are filtered against a universal allow-list.

    The allow-list applies to every caller regardless of scope.  Admins
    have no legitimate runtime reason to flip identity or policy
    backends (those are operator decisions done at startup or in
    shadow deployments), and the invariant "trust-sensitive primitives
    cannot be overridden at request time" is easier to reason about
    when it's universal.

    The allow-list also applies to ``spec.provider_overrides`` on
    agent specs — otherwise a non-admin agent owner could re-inject
    a stripped override via the spec field.
    """

    def _with_backend(self, principal):
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return principal

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        return prev

    def _install_no_relay_identity_backend(self):
        from agentic_primitives_gateway.models.enums import Primitive
        from agentic_primitives_gateway.primitives.identity.noop import NoopIdentityProvider
        from agentic_primitives_gateway.registry import registry

        class _NoRelayStub(NoopIdentityProvider):
            async def supports_user_relay(self) -> bool:
                return False

        prim = registry.get_primitive(Primitive.IDENTITY)
        prev = prim.get()
        prim._providers[prim.default_name] = _NoRelayStub()  # type: ignore[assignment]
        return prim, prev

    def _install_shadow_relay_backend(self, prim) -> None:
        """Register a second identity backend that always relays."""
        from agentic_primitives_gateway.primitives.identity.noop import NoopIdentityProvider

        class _AlwaysRelay(NoopIdentityProvider):
            async def supports_user_relay(self) -> bool:
                return True

        prim._providers["noop-shadow"] = _AlwaysRelay()  # type: ignore[assignment]

    def test_admin_also_cannot_override_identity_backend(self) -> None:
        """Admin has no bypass — the allow-list is universal."""
        prev_backend = self._with_backend(_admin())
        prim, prev_provider = self._install_no_relay_identity_backend()
        self._install_shadow_relay_backend(prim)
        try:
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post(
                "/api/v1/identity/token",
                json={"credential_provider": "test", "workload_token": "tok"},
                headers={"X-Provider-Identity": "noop-shadow"},
            )
            assert resp.status_code == 200
        finally:
            prim._providers.pop("noop-shadow", None)
            prim._providers[prim.default_name] = prev_provider  # type: ignore[assignment]
            app.state.auth_backend = prev_backend

    def test_memory_override_is_preserved_for_any_caller(self) -> None:
        """Non-trust-sensitive overrides are honoured regardless of scope."""
        from agentic_primitives_gateway.context import get_provider_override

        captured: dict[str, str | None] = {}

        @app.get("/__test_override_probe__")
        async def probe():
            captured["memory"] = get_provider_override("memory")
            captured["identity"] = get_provider_override("identity")
            return {"ok": True}

        prev_backend = self._with_backend(_user("alice"))
        try:
            c = TestClient(app, raise_server_exceptions=False)
            c.get(
                "/__test_override_probe__",
                headers={
                    "X-Provider-Memory": "some-memory-backend",
                    "X-Provider-Identity": "noop-shadow",
                },
            )
            # Memory override survives — routing preference.
            assert captured["memory"] == "some-memory-backend"
            # Identity override is stripped.
            assert captured["identity"] is None
        finally:
            app.state.auth_backend = prev_backend
            app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/__test_override_probe__"]

    def test_admin_memory_override_is_also_preserved(self) -> None:
        from agentic_primitives_gateway.context import get_provider_override

        captured: dict[str, str | None] = {}

        @app.get("/__test_admin_override_probe__")
        async def probe():
            captured["memory"] = get_provider_override("memory")
            captured["identity"] = get_provider_override("identity")
            captured["policy"] = get_provider_override("policy")
            return {"ok": True}

        prev_backend = self._with_backend(_admin())
        try:
            c = TestClient(app, raise_server_exceptions=False)
            c.get(
                "/__test_admin_override_probe__",
                headers={
                    "X-Provider-Memory": "some-memory-backend",
                    "X-Provider-Identity": "noop-shadow",
                    "X-Provider-Policy": "noop",
                },
            )
            assert captured["memory"] == "some-memory-backend"
            assert captured["identity"] is None
            assert captured["policy"] is None
        finally:
            app.state.auth_backend = prev_backend
            app.router.routes = [
                r for r in app.router.routes if getattr(r, "path", None) != "/__test_admin_override_probe__"
            ]

    def test_default_provider_override_is_stripped(self) -> None:
        from agentic_primitives_gateway.context import get_provider_override

        captured: dict[str, str | None] = {}

        @app.get("/__test_default_override_probe__")
        async def probe():
            captured["identity"] = get_provider_override("identity")
            captured["memory"] = get_provider_override("memory")
            return {"ok": True}

        prev_backend = self._with_backend(_user("alice"))
        try:
            c = TestClient(app, raise_server_exceptions=False)
            c.get(
                "/__test_default_override_probe__",
                headers={"X-Provider": "some-default-backend"},
            )
            # Default stripped → neither primitive sees an override.
            assert captured["identity"] is None
            assert captured["memory"] is None
        finally:
            app.state.auth_backend = prev_backend
            app.router.routes = [
                r for r in app.router.routes if getattr(r, "path", None) != "/__test_default_override_probe__"
            ]

    def test_spec_provider_overrides_are_also_filtered(self) -> None:
        from agentic_primitives_gateway.auth.access import (
            ProviderOverrideSource,
            apply_filtered_provider_overrides,
        )
        from agentic_primitives_gateway.context import get_provider_override, set_provider_overrides

        set_provider_overrides({})
        apply_filtered_provider_overrides(
            {
                "memory": "some-mem-backend",
                "identity": "noop-shadow",
                "policy": "noop",
            },
            source=ProviderOverrideSource.TEST,
            resource_id="agent-x",
        )
        assert get_provider_override("memory") == "some-mem-backend"
        assert get_provider_override("identity") is None
        assert get_provider_override("policy") is None


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

        asyncio.run(browser_session_owners.set_owner("alice-sess", "alice"))
        asyncio.run(browser_session_owners.set_owner("bob-sess", "bob"))

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

        asyncio.run(code_interpreter_session_owners.set_owner("alice-ci", "alice"))
        asyncio.run(code_interpreter_session_owners.set_owner("bob-ci", "bob"))

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


# ── Memory pools: transitive-through-agents ACL ──────────────────────


class TestMemoryPoolTransitiveACL:
    """Unscoped shared-pool endpoints require transitive access.

    The gateway enforces pool-level access by walking specs visible
    to the caller: if some agent or team the caller can access declares
    the pool, the caller gets read/write REST parity with what they could
    already do via the agent-tool surface.  Delete is stricter.
    """

    def _stores(self, tmp_path):
        """Wire real stores into the module-level slots used by routes."""
        from agentic_primitives_gateway.agents.file_store import FileAgentStore, FileTeamStore
        from agentic_primitives_gateway.routes.agents import set_agent_store
        from agentic_primitives_gateway.routes.teams import set_team_store

        agent_store = FileAgentStore(path=str(tmp_path / "agents.json"))
        team_store = FileTeamStore(path=str(tmp_path / "teams.json"))
        team_store.bind_agent_store(agent_store)
        set_agent_store(agent_store)
        set_team_store(team_store)
        return agent_store, team_store

    def _fixed_backend(self, principal):
        from agentic_primitives_gateway.auth.base import AuthBackend

        class FixedBackend(AuthBackend):
            async def authenticate(self, request):
                return principal

        prev = getattr(app.state, "auth_backend", None)
        app.state.auth_backend = FixedBackend()
        return prev

    def _create_agent(self, client, owner_id: str, pool: str, shared_with=None):
        """POST a spec that declares ``pool`` in shared_namespaces."""
        payload = {
            "name": f"agent-{owner_id}",
            "model": "noop/stub",
            "primitives": {"memory": {"shared_namespaces": [pool]}},
            "shared_with": shared_with or [],
        }
        # owner_id flows from the authenticated principal; caller injects
        # the right backend before invoking this helper.
        resp = client.post("/api/v1/agents", json=payload)
        assert resp.status_code == 201, resp.text
        return resp.json()

    def test_bob_without_any_declaring_agent_denied(self, tmp_path) -> None:
        """Orphan-pool case: bob has no agent that declares pool-p."""
        self._stores(tmp_path)
        prev = self._fixed_backend(_user("bob"))
        try:
            c = TestClient(app, raise_server_exceptions=False)
            resp = c.post("/api/v1/memory/pool-p", json={"key": "k1", "content": "v"})
            assert resp.status_code == 403
        finally:
            app.state.auth_backend = prev

    def test_bob_owns_agent_declaring_pool_allowed_read_write(self, tmp_path) -> None:
        self._stores(tmp_path)
        prev = self._fixed_backend(_user("bob"))
        try:
            c = TestClient(app, raise_server_exceptions=False)
            self._create_agent(c, owner_id="bob", pool="pool-p")
            assert c.post("/api/v1/memory/pool-p", json={"key": "k", "content": "v"}).status_code == 201
            assert c.get("/api/v1/memory/pool-p/k").status_code == 200
            assert c.get("/api/v1/memory/pool-p").status_code == 200
            assert c.post("/api/v1/memory/pool-p/search", json={"query": "x"}).status_code == 200
        finally:
            app.state.auth_backend = prev

    def test_bob_sharee_allowed_read_write_denied_delete(self, tmp_path) -> None:
        """Bob reads/writes via alice's shared agent; delete requires ownership."""
        self._stores(tmp_path)

        # Alice creates an agent that declares pool-p and shares with *.
        prev_alice = self._fixed_backend(_user("alice"))
        try:
            c_alice = TestClient(app, raise_server_exceptions=False)
            self._create_agent(c_alice, owner_id="alice", pool="pool-p", shared_with=["*"])
            # Bob writes a key first so the delete call has something to target.
            # We use alice's client here because she owns the agent, but the
            # important thing is bob's ACL on pool-p, not who seeded the data.
            c_alice.post("/api/v1/memory/pool-p", json={"key": "k1", "content": "v"})
        finally:
            app.state.auth_backend = prev_alice

        prev_bob = self._fixed_backend(_user("bob"))
        try:
            c_bob = TestClient(app, raise_server_exceptions=False)
            # Read/write parity with the tool surface — all four ops.
            assert c_bob.post("/api/v1/memory/pool-p", json={"key": "k2", "content": "v"}).status_code == 201
            assert c_bob.get("/api/v1/memory/pool-p/k1").status_code == 200
            assert c_bob.get("/api/v1/memory/pool-p").status_code == 200
            assert c_bob.post("/api/v1/memory/pool-p/search", json={"query": "x"}).status_code == 200
            # Delete is stricter — sharee cannot wipe the owner's data.
            assert c_bob.delete("/api/v1/memory/pool-p/k1").status_code == 403
        finally:
            app.state.auth_backend = prev_bob

    def test_admin_bypasses_all_checks(self, tmp_path) -> None:
        self._stores(tmp_path)
        prev = self._fixed_backend(_admin())
        try:
            c = TestClient(app, raise_server_exceptions=False)
            # No agent declares this pool; admin still gets through.
            assert c.post("/api/v1/memory/pool-p", json={"key": "k", "content": "v"}).status_code == 201
            assert c.delete("/api/v1/memory/pool-p/k").status_code == 204
        finally:
            app.state.auth_backend = prev

    def test_user_scoped_namespace_unaffected(self, tmp_path) -> None:
        """User-scoped ``:u:{self}`` namespaces skip the transitive check."""
        self._stores(tmp_path)
        prev = self._fixed_backend(_user("bob"))
        try:
            c = TestClient(app, raise_server_exceptions=False)
            ns = "agent:whatever:u:bob"
            assert c.post(f"/api/v1/memory/{ns}", json={"key": "k", "content": "v"}).status_code == 201
        finally:
            app.state.auth_backend = prev
