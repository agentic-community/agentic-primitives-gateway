"""Conftest for client-roundtrip integration tests.

Overrides the AWS-requiring parent conftest with noop/in_memory
providers so these tests run without any cloud dependencies.
Their only goal is to verify the client round-trips through the
real ASGI app — not to test external services.
"""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.registry import registry


@pytest.fixture(autouse=True)
def _init_registry(monkeypatch):
    """Initialize registry with in-memory/noop providers.  Overrides
    the parent conftest's AgentCore-requiring fixture.
    """
    # Neutralize the parent's AWS gate.
    monkeypatch.setattr(
        "tests.integration.conftest._skip_without_aws_credentials",
        lambda: None,
        raising=False,
    )
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
        }
    )
    registry.initialize(test_settings)
