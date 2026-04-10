from __future__ import annotations

import asyncio
import textwrap

import pytest
from fastapi.testclient import TestClient

from agentic_primitives_gateway import watcher as watcher_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.watcher import ConfigWatcher, get_last_reload_error

# ── reload() tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reload_swaps_providers():
    """reload() replaces providers with new instances."""
    old_memory = registry.get_primitive("memory")
    new_settings = Settings()
    await registry.reload(new_settings)
    new_memory = registry.get_primitive("memory")
    assert new_memory is not old_memory


@pytest.mark.asyncio
async def test_reload_keeps_old_on_bad_class_path():
    """reload() raises and keeps old providers when a class path is invalid."""
    old_memory = registry.get_primitive("memory")
    bad_settings = Settings(
        providers={
            "memory": {
                "backend": "nonexistent.module.FakeProvider",
                "config": {},
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "llm": {
                "backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider",
                "config": {},
            },
            "tools": {
                "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
                "config": {},
            },
            "identity": {
                "backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider",
                "config": {},
            },
            "code_interpreter": {
                "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider",
                "config": {},
            },
            "browser": {
                "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
                "config": {},
            },
        }
    )
    with pytest.raises(ModuleNotFoundError):
        await registry.reload(bad_settings)
    # Old providers should still be in place
    assert registry.get_primitive("memory") is old_memory


@pytest.mark.asyncio
async def test_reload_keeps_old_on_type_mismatch():
    """reload() raises TypeError and keeps old providers on type mismatch."""
    old_memory = registry.get_primitive("memory")
    bad_settings = Settings(
        providers={
            "memory": {
                # Use an observability provider for memory — type mismatch
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "llm": {
                "backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider",
                "config": {},
            },
            "tools": {
                "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
                "config": {},
            },
            "identity": {
                "backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider",
                "config": {},
            },
            "code_interpreter": {
                "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider",
                "config": {},
            },
            "browser": {
                "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
                "config": {},
            },
        }
    )
    with pytest.raises(TypeError):
        await registry.reload(bad_settings)
    assert registry.get_primitive("memory") is old_memory


@pytest.mark.asyncio
async def test_reload_calls_close_on_old_providers():
    """reload() calls close() on old providers that have it."""
    closed = []

    class FakeProvider:
        def close(self) -> None:
            closed.append(True)

    # Inject a fake provider with close() into the registry's current primitives
    old_prim = registry.get_primitive("memory")
    original_provider = old_prim._providers[old_prim.default_name]
    original_provider._provider.close = FakeProvider().close  # type: ignore[attr-defined]

    new_settings = Settings()
    await registry.reload(new_settings)

    assert len(closed) == 1


# ── ConfigWatcher tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_watcher_detects_file_change(tmp_path):
    """ConfigWatcher detects when a config file is modified."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        textwrap.dedent("""\
        host: "0.0.0.0"
        port: 8000
    """)
    )

    reload_count = 0
    original_reload = registry.reload

    async def counting_reload(new_settings: Settings) -> None:
        nonlocal reload_count
        reload_count += 1
        await original_reload(new_settings)

    registry.reload = counting_reload  # type: ignore[assignment]

    try:
        watcher = ConfigWatcher(str(config_file), registry, poll_interval=0.1)
        await watcher.start()

        # Give time for initial stat
        await asyncio.sleep(0.15)

        # Modify the file
        config_file.write_text(
            textwrap.dedent("""\
            host: "0.0.0.0"
            port: 9999
        """)
        )

        # Wait for detection
        await asyncio.sleep(0.3)
        await watcher.stop()

        assert reload_count >= 1
    finally:
        registry.reload = original_reload  # type: ignore[assignment]


@pytest.mark.asyncio
async def test_watcher_ignores_unchanged_file(tmp_path):
    """ConfigWatcher does not reload when the file is unchanged."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("host: '0.0.0.0'\n")

    reload_count = 0
    original_reload = registry.reload

    async def counting_reload(new_settings: Settings) -> None:
        nonlocal reload_count
        reload_count += 1

    registry.reload = counting_reload  # type: ignore[assignment]

    try:
        watcher = ConfigWatcher(str(config_file), registry, poll_interval=0.1)
        await watcher.start()

        # Wait several poll cycles without changing the file
        await asyncio.sleep(0.5)
        await watcher.stop()

        assert reload_count == 0
    finally:
        registry.reload = original_reload  # type: ignore[assignment]


# ── /readyz reload error tests ───────────────────────────────────


def test_readyz_reports_degraded_on_reload_error():
    """/readyz returns DEGRADED / 503 when a reload error is set."""
    watcher_module._last_reload_error = "Config reload failed for /etc/config.yaml"
    client = TestClient(app)
    resp = client.get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert "config_reload_error" in body


def test_readyz_ok_after_error_cleared():
    """/readyz returns OK when reload error is cleared."""
    watcher_module._last_reload_error = None
    client = TestClient(app)
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── get_last_reload_error tests ──────────────────────────────────


def test_get_last_reload_error_initially_none():
    assert get_last_reload_error() is None


def test_get_last_reload_error_returns_message():
    watcher_module._last_reload_error = "something failed"
    assert get_last_reload_error() == "something failed"
