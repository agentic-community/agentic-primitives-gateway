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
        set_provider_overrides({"llm": "bedrock"})
        spec = AgentSpec(
            name="test",
            model="test-model",
            provider_overrides={"memory": "mem0"},
        )
        prev = AgentRunner._apply_overrides(spec)
        assert prev == {"llm": "bedrock"}
        # Both should be active
        assert get_provider_override("memory") == "mem0"
        assert get_provider_override("llm") == "bedrock"
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
        set_provider_overrides({"llm": "bedrock"})
        spec = AgentSpec(name="test", model="test-model")
        AgentRunner._apply_overrides(spec)
        # Previous state preserved
        assert get_provider_override("llm") == "bedrock"
        # Clean up
        set_provider_overrides({})


class TestRestoreOverrides:
    def test_restores_previous_state(self) -> None:
        original = {"llm": "bedrock", "memory": "in_memory"}
        set_provider_overrides({"memory": "mem0", "browser": "selenium_grid"})
        AgentRunner._restore_overrides(original)
        assert get_provider_override("memory") == "in_memory"
        assert get_provider_override("llm") == "bedrock"
        assert get_provider_override("browser") is None
        # Clean up
        set_provider_overrides({})

    def test_restores_empty(self) -> None:
        set_provider_overrides({"memory": "mem0"})
        AgentRunner._restore_overrides({})
        assert get_provider_override("memory") is None
        # Clean up
        set_provider_overrides({})


class TestApplyOverridesFiltersTrustSensitive:
    """Sub-agent delegation must not admit trust-sensitive overrides from a spec."""

    def test_identity_override_in_spec_is_filtered(self) -> None:
        set_provider_overrides({})
        spec = AgentSpec(
            name="child",
            model="test-model",
            provider_overrides={
                "memory": "mem0",
                "identity": "noop-shadow",
                "policy": "noop",
            },
        )
        AgentRunner._apply_overrides(spec)
        # Allow-listed key survived.
        assert get_provider_override("memory") == "mem0"
        # Trust-sensitive keys were dropped before reaching the contextvar.
        assert get_provider_override("identity") is None
        assert get_provider_override("policy") is None
        set_provider_overrides({})

    def test_parent_allowlisted_overrides_are_preserved_after_filter(self) -> None:
        """Merging parent + filtered spec must not wipe parent's prior overrides."""
        set_provider_overrides({"llm": "bedrock"})
        spec = AgentSpec(
            name="child",
            model="test-model",
            # Entirely trust-sensitive — nothing from the spec survives the filter.
            provider_overrides={"identity": "noop-shadow"},
        )
        AgentRunner._apply_overrides(spec)
        # Parent's llm override is still in effect because the merged
        # dict included it and ``llm`` is on the allow-list.
        assert get_provider_override("llm") == "bedrock"
        set_provider_overrides({})

    def test_trust_sensitive_key_in_parent_is_also_filtered(self) -> None:
        """The delegation filter strips trust-sensitive keys regardless of
        which layer they came from.

        The auth middleware already strips ``identity`` / ``policy`` at
        the request boundary, but if one somehow reached the contextvar
        through another path, the delegation filter catches it again.
        A parent contextvar with ``identity`` present is stripped when
        a sub-agent's spec is applied — the filter runs on the merged
        dict, not only on the spec's contribution.
        """
        set_provider_overrides({"identity": "leaked-from-somewhere", "llm": "bedrock"})
        spec = AgentSpec(
            name="child",
            model="test-model",
            provider_overrides={"memory": "mem0"},
        )
        AgentRunner._apply_overrides(spec)
        # Spec's allow-listed key applied.
        assert get_provider_override("memory") == "mem0"
        # Parent's allow-listed key preserved.
        assert get_provider_override("llm") == "bedrock"
        # Parent's trust-sensitive key was stripped by the filter too.
        assert get_provider_override("identity") is None
        set_provider_overrides({})
