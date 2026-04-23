"""Intent-level test: hot-reload swaps the provider instance at runtime.

Contract (CLAUDE.md "Config file hot-reload watcher"): editing the
config YAML at runtime triggers ``registry.reload(new_settings)``,
which replaces the provider bindings atomically.  Subsequent
``registry.memory`` (or any other primitive) returns the new
backend instance.

Observable breakage: if ``reload`` silently failed (wrong class
path, broken instantiation) AND didn't raise, the registry would
keep serving the old provider while the config file showed the
new one — a split-brain state that's hard to diagnose because
``/readyz`` would show healthy.

No existing test verifies reload from the operator's perspective:
before → call reload → after.  This file covers the positive swap
and the atomicity of the failure path.
"""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.primitives.memory.in_memory import InMemoryProvider
from agentic_primitives_gateway.primitives.memory.noop import NoopMemoryProvider
from agentic_primitives_gateway.registry import ProviderRegistry


def _settings_with_memory_backend(memory_backend: str) -> Settings:
    """Build Settings with the given memory backend and noop
    providers for everything else.  Uses the shorthand
    ``{backend, config}`` shape that PrimitiveProvidersConfig
    auto-normalizes to the multi-provider form.
    """
    providers_dict = {
        "memory": {"backend": memory_backend, "config": {}},
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
    return Settings(providers=providers_dict)


class TestHotReloadSwap:
    @pytest.mark.asyncio
    async def test_reload_swaps_memory_provider_class(self):
        """Start with NoopMemoryProvider; reload with InMemoryProvider;
        ``registry.memory`` now returns the new class.
        """
        registry = ProviderRegistry()

        # Bootstrap with noop.
        initial = _settings_with_memory_backend("agentic_primitives_gateway.primitives.memory.noop.NoopMemoryProvider")
        registry.initialize(initial)
        # Unwrap the MetricsProxy to inspect the real class.
        before = registry.memory._provider  # type: ignore[attr-defined]
        assert isinstance(before, NoopMemoryProvider)

        # Reload with InMemoryProvider.
        reloaded = _settings_with_memory_backend(
            "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
        )
        await registry.reload(reloaded)

        after = registry.memory._provider  # type: ignore[attr-defined]
        assert isinstance(after, InMemoryProvider), (
            f"After reload, expected InMemoryProvider; got {type(after).__name__}"
        )
        # And the instance is NEW — not the old one hanging around.
        assert after is not before

    @pytest.mark.asyncio
    async def test_reload_failure_keeps_old_providers(self):
        """If the new config references a broken class path, reload
        raises AND the registry keeps serving the old providers.
        An atomic swap is the contract (CLAUDE.md registry reload
        three-phase approach: "if any fails, old providers stay").
        """
        registry = ProviderRegistry()
        registry.initialize(
            _settings_with_memory_backend("agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider")
        )
        original = registry.memory._provider  # type: ignore[attr-defined]

        # Config that won't load (nonexistent class).
        broken = _settings_with_memory_backend(
            "agentic_primitives_gateway.primitives.memory.nonexistent.DoesNotExistProvider"
        )
        with pytest.raises((ImportError, ModuleNotFoundError, AttributeError)):
            await registry.reload(broken)

        # registry.memory still points at the original.
        assert registry.memory._provider is original, (
            "reload failure left the registry with partial state — atomicity broken"
        )

    @pytest.mark.asyncio
    async def test_reload_preserves_uninvolved_primitives(self):
        """Swapping the memory backend doesn't churn unrelated
        primitives — observability, llm, etc. should stay stable
        if their config didn't change.  (Not a strict equality
        invariant — reload rebuilds everything — but the contract
        is that unrelated primitives still work after reload.)
        """
        registry = ProviderRegistry()
        registry.initialize(
            _settings_with_memory_backend("agentic_primitives_gateway.primitives.memory.noop.NoopMemoryProvider")
        )
        before_obs_class = type(registry.observability._provider)  # type: ignore[attr-defined]

        await registry.reload(
            _settings_with_memory_backend("agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider")
        )
        after_obs_class = type(registry.observability._provider)  # type: ignore[attr-defined]
        assert before_obs_class is after_obs_class
