from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from agentic_primitives_gateway import watcher as _watcher_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry


@pytest.fixture(autouse=True, scope="session")
def _suppress_asyncio_teardown_noise():
    """Suppress asyncio event loop teardown logging noise.

    When tests use streaming endpoints via TestClient, pending asyncio tasks
    (e.g. sleep coroutines in reconnect_event_generator) may not be fully
    cancelled before the event loop shuts down.  This causes harmless
    "Task was destroyed but it is pending" messages that write to stderr
    after pytest has already closed it.  Silence the asyncio logger and
    suppress the coroutine RuntimeWarning to prevent spurious errors.
    """

    yield
    logging.getLogger("asyncio").setLevel(logging.CRITICAL)


@pytest.fixture(autouse=True)
def _reset_reload_error() -> None:
    """Clear any stale reload error between tests."""
    _watcher_module._last_reload_error = None


@pytest.fixture(autouse=True)
def _init_registry() -> None:
    """Initialize registry with in-memory/noop providers for all tests."""
    test_settings = Settings(
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider",
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
            "policy": {
                "backend": "agentic_primitives_gateway.primitives.policy.noop.NoopPolicyProvider",
                "config": {},
            },
            "evaluations": {
                "backend": "agentic_primitives_gateway.primitives.evaluations.noop.NoopEvaluationsProvider",
                "config": {},
            },
            "tasks": {
                "backend": "agentic_primitives_gateway.primitives.tasks.noop.NoopTasksProvider",
                "config": {},
            },
            "knowledge": {
                "backend": "agentic_primitives_gateway.primitives.knowledge.noop.NoopKnowledgeProvider",
                "config": {},
            },
        }
    )
    registry.initialize(test_settings)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
