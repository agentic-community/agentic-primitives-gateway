"""Tests for provider override application and restoration in the agent runner."""

from __future__ import annotations

from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.context import get_provider_override, set_provider_overrides
from agentic_primitives_gateway.models.agents import AgentSpec


class TestApplyOverrides:
    def test_applies_overrides(self) -> None:
        set_provider_overrides({})
        spec = AgentSpec(
            name="test",
            model="test-model",
            provider_overrides={"memory": "mem0", "browser": "selenium_grid"},
        )
        prev = AgentRunner._apply_overrides(spec)
        assert prev == {}
        assert get_provider_override("memory") == "mem0"
        assert get_provider_override("browser") == "selenium_grid"
        # Clean up
        set_provider_overrides({})

    def test_merges_with_existing(self) -> None:
        set_provider_overrides({"gateway": "bedrock"})
        spec = AgentSpec(
            name="test",
            model="test-model",
            provider_overrides={"memory": "mem0"},
        )
        prev = AgentRunner._apply_overrides(spec)
        assert prev == {"gateway": "bedrock"}
        # Both should be active
        assert get_provider_override("memory") == "mem0"
        assert get_provider_override("gateway") == "bedrock"
        # Clean up
        set_provider_overrides({})

    def test_agent_overrides_take_priority(self) -> None:
        set_provider_overrides({"memory": "in_memory"})
        spec = AgentSpec(
            name="test",
            model="test-model",
            provider_overrides={"memory": "mem0"},
        )
        prev = AgentRunner._apply_overrides(spec)
        assert prev == {"memory": "in_memory"}
        assert get_provider_override("memory") == "mem0"
        # Clean up
        set_provider_overrides({})

    def test_no_overrides_is_noop(self) -> None:
        set_provider_overrides({"gateway": "bedrock"})
        spec = AgentSpec(name="test", model="test-model")
        AgentRunner._apply_overrides(spec)
        # Previous state preserved
        assert get_provider_override("gateway") == "bedrock"
        # Clean up
        set_provider_overrides({})


class TestRestoreOverrides:
    def test_restores_previous_state(self) -> None:
        original = {"gateway": "bedrock", "memory": "in_memory"}
        set_provider_overrides({"memory": "mem0", "browser": "selenium_grid"})
        AgentRunner._restore_overrides(original)
        assert get_provider_override("memory") == "in_memory"
        assert get_provider_override("gateway") == "bedrock"
        assert get_provider_override("browser") is None
        # Clean up
        set_provider_overrides({})

    def test_restores_empty(self) -> None:
        set_provider_overrides({"memory": "mem0"})
        AgentRunner._restore_overrides({})
        assert get_provider_override("memory") is None
        # Clean up
        set_provider_overrides({})
