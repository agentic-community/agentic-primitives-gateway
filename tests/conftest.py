from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentic_primitives_gateway import watcher as _watcher_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry


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
            "gateway": {
                "backend": "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider",
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
        }
    )
    registry.initialize(test_settings)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
