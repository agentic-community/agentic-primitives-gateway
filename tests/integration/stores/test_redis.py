"""Integration tests for Redis-backed stores against a real Redis server.

Covers RedisAgentStore, RedisTeamStore, RedisSessionRegistry, and RedisEventStore.

Requires Redis running at localhost:6379 (or REDIS_URL env var).

Run with:
    python -m pytest tests/integration/test_redis_stores.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest

from agentic_primitives_gateway.agents.redis_store import RedisAgentStore, RedisTeamStore
from agentic_primitives_gateway.agents.session_registry import RedisSessionRegistry
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.models.agents import AgentSpec
from agentic_primitives_gateway.models.teams import TeamSpec
from agentic_primitives_gateway.routes._background import RedisEventStore

pytestmark = pytest.mark.integration

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

_ALICE = AuthenticatedPrincipal(id="alice", type="user", groups=frozenset({"engineering"}), scopes=frozenset())
_BOB = AuthenticatedPrincipal(id="bob", type="user", groups=frozenset({"sales"}), scopes=frozenset())


def _redis_available() -> bool:
    try:
        import redis

        r = redis.from_url(REDIS_URL)
        r.ping()
        r.close()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _skip_without_redis():
    if not _redis_available():
        pytest.skip("Redis not available — skipping Redis store integration tests")


def _uid() -> str:
    """Short unique suffix to avoid key collisions between tests."""
    return uuid.uuid4().hex[:8]


# ── RedisAgentStore ──────────────────────────────────────────────────


class TestRedisAgentStoreIntegration:
    @pytest.fixture
    async def store(self):
        s = RedisAgentStore(redis_url=REDIS_URL)
        created: list[str] = []
        yield s, created
        # Cleanup: delete all agents created during the test
        for name in created:
            await s.delete(name)

    @pytest.mark.asyncio
    async def test_create_and_get(self, store):
        s, created = store
        name = f"integ-agent-{_uid()}"
        created.append(name)

        spec = AgentSpec(name=name, model="test-model", owner_id="alice", shared_with=["engineering"])
        await s.create(spec)

        result = await s.get(name)
        assert result is not None
        assert result.name == name
        assert result.model == "test-model"
        assert result.owner_id == "alice"
        assert result.shared_with == ["engineering"]

    @pytest.mark.asyncio
    async def test_list(self, store):
        s, created = store
        name1 = f"integ-list-a-{_uid()}"
        name2 = f"integ-list-b-{_uid()}"
        created.extend([name1, name2])

        await s.create(AgentSpec(name=name1, model="m"))
        await s.create(AgentSpec(name=name2, model="m"))

        agents = await s.list()
        names = {a.name for a in agents}
        assert name1 in names
        assert name2 in names

    @pytest.mark.asyncio
    async def test_list_for_user(self, store):
        s, created = store
        uid = _uid()
        alice_agent = f"integ-alice-{uid}"
        bob_agent = f"integ-bob-{uid}"
        shared_agent = f"integ-shared-{uid}"
        created.extend([alice_agent, bob_agent, shared_agent])

        await s.create(AgentSpec(name=alice_agent, model="m", owner_id="alice", shared_with=[]))
        await s.create(AgentSpec(name=bob_agent, model="m", owner_id="bob", shared_with=[]))
        await s.create(AgentSpec(name=shared_agent, model="m", owner_id="bob", shared_with=["*"]))

        alice_agents = await s.list_for_user(_ALICE)
        alice_names = {a.name for a in alice_agents}
        # Alice should see her own agent and the wildcard-shared agent
        assert alice_agent in alice_names
        assert shared_agent in alice_names
        # Alice should NOT see Bob's private agent
        assert bob_agent not in alice_names

    @pytest.mark.asyncio
    async def test_update(self, store):
        s, created = store
        name = f"integ-update-{_uid()}"
        created.append(name)

        await s.create(AgentSpec(name=name, model="m1"))
        updated = await s.update(name, {"model": "m2"})
        assert updated.model == "m2"

        fetched = await s.get(name)
        assert fetched is not None
        assert fetched.model == "m2"

    @pytest.mark.asyncio
    async def test_delete(self, store):
        s, _created = store
        name = f"integ-delete-{_uid()}"
        # No need to add to created — we delete in the test itself

        await s.create(AgentSpec(name=name, model="m"))
        assert await s.delete(name) is True
        assert await s.get(name) is None

    @pytest.mark.asyncio
    async def test_seed(self, store):
        s, created = store
        name = f"integ-seed-{_uid()}"
        created.append(name)

        s.seed({name: {"model": "seed-model"}})
        # seed runs async inside; give it a moment
        import asyncio

        await asyncio.sleep(0.5)

        result = await s.get(name)
        assert result is not None
        assert result.model == "seed-model"
        # Config-seeded agents should default to shared_with: ["*"]
        assert result.shared_with == ["*"]


# ── RedisTeamStore ───────────────────────────────────────────────────


class TestRedisTeamStoreIntegration:
    @pytest.fixture
    async def store(self):
        s = RedisTeamStore(redis_url=REDIS_URL)
        created: list[str] = []
        yield s, created
        for name in created:
            await s.delete(name)

    @pytest.mark.asyncio
    async def test_create_and_get(self, store):
        s, created = store
        name = f"integ-team-{_uid()}"
        created.append(name)

        spec = TeamSpec(name=name, planner="p", synthesizer="s", workers=["w"])
        await s.create(spec)

        result = await s.get(name)
        assert result is not None
        assert result.name == name
        assert result.planner == "p"

    @pytest.mark.asyncio
    async def test_list_for_user(self, store):
        s, created = store
        uid = _uid()
        alice_team = f"integ-ateam-{uid}"
        shared_team = f"integ-steam-{uid}"
        created.extend([alice_team, shared_team])

        await s.create(
            TeamSpec(name=alice_team, planner="p", synthesizer="s", workers=["w"], owner_id="alice", shared_with=[])
        )
        await s.create(
            TeamSpec(name=shared_team, planner="p", synthesizer="s", workers=["w"], owner_id="bob", shared_with=["*"])
        )

        alice_teams = await s.list_for_user(_ALICE)
        alice_names = {t.name for t in alice_teams}
        assert alice_team in alice_names
        assert shared_team in alice_names

        bob_teams = await s.list_for_user(_BOB)
        bob_names = {t.name for t in bob_teams}
        assert alice_team not in bob_names
        assert shared_team in bob_names

    @pytest.mark.asyncio
    async def test_seed_injects_wildcard(self, store):
        s, created = store
        name = f"integ-tseed-{_uid()}"
        created.append(name)

        s.seed({name: {"planner": "p", "synthesizer": "s", "workers": ["w"]}})
        import asyncio

        await asyncio.sleep(0.5)

        result = await s.get(name)
        assert result is not None
        assert result.shared_with == ["*"]


# ── RedisSessionRegistry ────────────────────────────────────────────


class TestRedisSessionRegistryIntegration:
    @pytest.fixture
    async def registry(self):
        r = RedisSessionRegistry(redis_url=REDIS_URL, ttl=60)
        registered: list[tuple[str, str]] = []
        yield r, registered
        # Cleanup
        for primitive, session_id in registered:
            await r.unregister(primitive, session_id)

    @pytest.mark.asyncio
    async def test_register_and_list(self, registry):
        reg, registered = registry
        uid = _uid()
        primitive = f"browser-{uid}"
        session_id = f"sess-{uid}"
        registered.append((primitive, session_id))

        await reg.register(primitive, session_id, metadata={"user_id": "alice"})

        sessions = await reg.list_sessions(primitive)
        assert len(sessions) >= 1
        match = [s for s in sessions if s["session_id"] == session_id]
        assert len(match) == 1
        assert match[0]["user_id"] == "alice"
        assert match[0]["primitive"] == primitive

    @pytest.mark.asyncio
    async def test_unregister(self, registry):
        reg, _registered = registry
        uid = _uid()
        primitive = f"browser-{uid}"
        session_id = f"sess-{uid}"

        await reg.register(primitive, session_id)
        assert await reg.is_registered(primitive, session_id) is True

        await reg.unregister(primitive, session_id)
        assert await reg.is_registered(primitive, session_id) is False

    @pytest.mark.asyncio
    async def test_is_registered(self, registry):
        reg, registered = registry
        uid = _uid()
        primitive = f"browser-{uid}"
        session_id = f"sess-{uid}"
        registered.append((primitive, session_id))

        assert await reg.is_registered(primitive, session_id) is False
        await reg.register(primitive, session_id)
        assert await reg.is_registered(primitive, session_id) is True


# ── RedisEventStore ─────────────────────────────────────────────────


class TestRedisEventStoreIntegration:
    @pytest.fixture
    async def event_store(self):
        s = RedisEventStore(redis_url=REDIS_URL)
        keys: list[str] = []
        yield s, keys
        # Cleanup
        for key in keys:
            await s.delete(key)

    @pytest.mark.asyncio
    async def test_set_and_get_status(self, event_store):
        s, keys = event_store
        key = f"integ-status-{_uid()}"
        keys.append(key)

        assert await s.get_status(key) is None
        await s.set_status(key, "running", ttl=60)
        assert await s.get_status(key) == "running"

        await s.set_status(key, "idle", ttl=60)
        assert await s.get_status(key) == "idle"

    @pytest.mark.asyncio
    async def test_append_and_get_events(self, event_store):
        s, keys = event_store
        key = f"integ-events-{_uid()}"
        keys.append(key)

        await s.append_event(key, {"type": "token", "text": "hello"}, ttl=60)
        await s.append_event(key, {"type": "token", "text": " world"}, ttl=60)
        await s.append_event(key, {"type": "done"}, ttl=60)

        events = await s.get_events(key)
        assert len(events) == 3
        assert events[0]["type"] == "token"
        assert events[0]["text"] == "hello"
        assert events[2]["type"] == "done"

    @pytest.mark.asyncio
    async def test_set_and_get_owner(self, event_store):
        s, keys = event_store
        key = f"integ-owner-{_uid()}"
        keys.append(key)

        assert await s.get_owner(key) is None
        await s.set_owner(key, "alice", ttl=60)
        assert await s.get_owner(key) == "alice"

    @pytest.mark.asyncio
    async def test_delete_cleans_all_keys(self, event_store):
        s, _keys = event_store
        key = f"integ-del-{_uid()}"
        # No need to add to keys — we delete in the test

        await s.set_status(key, "running", ttl=60)
        await s.append_event(key, {"type": "token"}, ttl=60)
        await s.set_owner(key, "alice", ttl=60)

        await s.delete(key)

        assert await s.get_status(key) is None
        assert await s.get_events(key) == []
        assert await s.get_owner(key) is None

    @pytest.mark.asyncio
    async def test_rename_key(self, event_store):
        s, keys = event_store
        old_key = f"integ-old-{_uid()}"
        new_key = f"integ-new-{_uid()}"
        keys.append(new_key)  # cleanup the renamed key

        await s.set_status(old_key, "running", ttl=60)
        await s.append_event(old_key, {"type": "token", "text": "hi"}, ttl=60)
        await s.set_owner(old_key, "alice", ttl=60)

        await s.rename_key(old_key, new_key)

        # Old keys should be gone
        assert await s.get_status(old_key) is None
        assert await s.get_events(old_key) == []
        assert await s.get_owner(old_key) is None

        # New keys should have the data
        assert await s.get_status(new_key) == "running"
        events = await s.get_events(new_key)
        assert len(events) == 1
        assert events[0]["text"] == "hi"
        assert await s.get_owner(new_key) == "alice"
