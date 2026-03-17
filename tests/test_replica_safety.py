"""Tests for multi-replica safety: startup warnings and run indexing."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.routes._background import EventStore, RedisEventStore

# ── 25a: Startup config warnings ─────────────────────────────────────


class TestReplicaUnsafeConfigWarning:
    """_warn_replica_unsafe_config logs when Redis is mixed with local backends."""

    def test_no_warning_when_fully_local(self, caplog: pytest.LogCaptureFixture) -> None:
        """No warning when everything is file/in-memory (single replica assumed)."""
        from agentic_primitives_gateway.main import _warn_replica_unsafe_config

        with _override_settings(agent_backend="file", team_backend="file"):
            with caplog.at_level(logging.WARNING):
                _warn_replica_unsafe_config()
            assert "Multi-replica config warning" not in caplog.text

    def test_no_warning_when_fully_redis(self, caplog: pytest.LogCaptureFixture) -> None:
        """No warning when everything is Redis."""
        from agentic_primitives_gateway.main import _warn_replica_unsafe_config

        with _override_settings(agent_backend="redis", team_backend="redis"):
            with caplog.at_level(logging.WARNING):
                _warn_replica_unsafe_config()
            assert "Multi-replica config warning" not in caplog.text

    def test_warning_when_agents_redis_teams_file(self, caplog: pytest.LogCaptureFixture) -> None:
        """Warning when agents use Redis but teams use file."""
        from agentic_primitives_gateway.main import _warn_replica_unsafe_config

        with _override_settings(agent_backend="redis", team_backend="file"):
            with caplog.at_level(logging.WARNING):
                _warn_replica_unsafe_config()
            assert "Multi-replica config warning" in caplog.text
            assert "teams.store.backend='file'" in caplog.text

    def test_warning_for_in_memory_primitive(self, caplog: pytest.LogCaptureFixture) -> None:
        """Warning when Redis stores are used but a primitive uses in-memory provider."""
        from agentic_primitives_gateway.main import _warn_replica_unsafe_config

        memory_backends = {
            "default": _mock_backend("agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"),
        }
        with _override_settings(agent_backend="redis", team_backend="redis", memory_backends=memory_backends):
            with caplog.at_level(logging.WARNING):
                _warn_replica_unsafe_config()
            assert "in_memory" in caplog.text


def _mock_backend(backend_path: str):
    """Create a mock ProviderConfig-like object."""
    from unittest.mock import MagicMock

    m = MagicMock()
    m.backend = backend_path
    return m


def _override_settings(
    agent_backend: str,
    team_backend: str,
    memory_backends: dict | None = None,
):
    """Context manager to temporarily override settings for _warn_replica_unsafe_config."""
    from unittest.mock import MagicMock

    mock_settings = MagicMock()
    mock_settings.agents.store.backend = agent_backend
    mock_settings.teams.store.backend = team_backend

    # Build a ProvidersConfig-like mock with typed fields
    mock_providers = MagicMock()
    prim_names = (
        "memory",
        "observability",
        "gateway",
        "tools",
        "identity",
        "code_interpreter",
        "browser",
        "policy",
        "evaluations",
        "tasks",
    )
    for name in prim_names:
        prim = MagicMock()
        if name == "memory" and memory_backends:
            prim.backends = memory_backends
        else:
            # Default backend is noop (not in_memory)
            prim.backends = {"default": _mock_backend("noop")}
        setattr(mock_providers, name, prim)

    mock_settings.providers = mock_providers

    return patch("agentic_primitives_gateway.main.settings", mock_settings)


# ── 25b: EventStore index methods ────────────────────────────────────


class TestEventStoreIndex:
    """Base EventStore index methods are no-ops."""

    @pytest.mark.asyncio
    async def test_base_add_to_index_noop(self) -> None:
        class StubStore(EventStore):
            async def set_status(self, key, status, ttl=600): ...
            async def get_status(self, key): ...
            async def append_event(self, key, event, ttl=600): ...
            async def get_events(self, key): ...
            async def delete(self, key): ...

        store = StubStore()
        await store.add_to_index("team:x:runs", "run-1")  # should not raise

    @pytest.mark.asyncio
    async def test_base_get_index_returns_empty(self) -> None:
        class StubStore(EventStore):
            async def set_status(self, key, status, ttl=600): ...
            async def get_status(self, key): ...
            async def append_event(self, key, event, ttl=600): ...
            async def get_events(self, key): ...
            async def delete(self, key): ...

        store = StubStore()
        result = await store.get_index("team:x:runs")
        assert result == []


class TestRedisEventStoreIndex:
    """RedisEventStore index methods use Redis sets."""

    @pytest.mark.asyncio
    async def test_add_to_index_and_get(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(return_value={"run-1", "run-2"})

        with patch("redis.asyncio.from_url", return_value=mock_redis):
            store = RedisEventStore(redis_url="redis://localhost:6379/0")

        await store.add_to_index("team:my-team:runs", "run-1", ttl=600)
        mock_redis.sadd.assert_called_once_with("index:team:my-team:runs", "run-1")
        mock_redis.expire.assert_called_with("index:team:my-team:runs", 600)

        result = await store.get_index("team:my-team:runs")
        assert set(result) == {"run-1", "run-2"}

    @pytest.mark.asyncio
    async def test_get_index_empty(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.smembers = AsyncMock(return_value=set())

        with patch("redis.asyncio.from_url", return_value=mock_redis):
            store = RedisEventStore(redis_url="redis://localhost:6379/0")

        result = await store.get_index("team:empty:runs")
        assert result == []


class TestBackgroundRunManagerIndex:
    """BackgroundRunManager passes index_key to EventStore on rekey."""

    @pytest.mark.asyncio
    async def test_index_key_registered_on_rekey(self) -> None:
        from agentic_primitives_gateway.routes._background import BackgroundRunManager

        mock_store = AsyncMock()
        mock_store.get_status = AsyncMock(return_value=None)

        bg = BackgroundRunManager(event_store=mock_store)

        async def _fake_stream():
            yield {"type": "team_start", "team_run_id": "actual-run-id", "team_name": "my-team"}
            yield {"type": "done", "response": "ok"}

        queue, _ = bg.start(
            "__pending",
            _fake_stream(),
            record_events=True,
            rekey_field="team_run_id",
            index_key="team:my-team:runs",
        )

        # Drain the queue
        events = []
        while True:
            event = await queue.get()
            if event is None:
                break
            events.append(event)

        # Verify index was registered
        mock_store.add_to_index.assert_called_once_with("team:my-team:runs", "actual-run-id", ttl=600)
        # Verify rename happened
        mock_store.rename_key.assert_called_once_with("__pending", "actual-run-id")
