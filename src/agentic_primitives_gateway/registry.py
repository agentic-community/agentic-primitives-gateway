from __future__ import annotations

import importlib
import logging
from typing import Any

from agentic_primitives_gateway.config import PrimitiveProvidersConfig, Settings, settings
from agentic_primitives_gateway.context import get_provider_override
from agentic_primitives_gateway.metrics import MetricsProxy
from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.primitives.base import (
    BrowserProvider,
    CodeInterpreterProvider,
    EvaluationsProvider,
    GatewayProvider,
    IdentityProvider,
    MemoryProvider,
    ObservabilityProvider,
    PolicyProvider,
    ToolsProvider,
)

logger = logging.getLogger(__name__)

PRIMITIVES = tuple(Primitive)

_EXPECTED_TYPES: dict[str, type] = {
    Primitive.MEMORY: MemoryProvider,
    Primitive.OBSERVABILITY: ObservabilityProvider,
    Primitive.GATEWAY: GatewayProvider,
    Primitive.TOOLS: ToolsProvider,
    Primitive.IDENTITY: IdentityProvider,
    Primitive.CODE_INTERPRETER: CodeInterpreterProvider,
    Primitive.BROWSER: BrowserProvider,
    Primitive.POLICY: PolicyProvider,
    Primitive.EVALUATIONS: EvaluationsProvider,
}


def _load_class(dotted_path: str) -> type:
    """Import a class from a dotted module path like 'pkg.module.ClassName'."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls: type = getattr(module, class_name)
    return cls


class _PrimitiveProviders:
    """Holds multiple named provider instances for a single primitive."""

    def __init__(
        self,
        primitive: str,
        default_name: str,
        providers: dict[str, Any],
    ) -> None:
        self._primitive = primitive
        self._default_name = default_name
        self._providers = providers

    @property
    def default_name(self) -> str:
        return self._default_name

    @property
    def names(self) -> list[str]:
        return list(self._providers.keys())

    def get(self, name: str | None = None) -> Any:
        """Get a provider by name, or the request-scoped override, or the default."""
        resolved = name or get_provider_override(self._primitive) or self._default_name
        provider = self._providers.get(resolved)
        if provider is None:
            available = ", ".join(self._providers.keys())
            raise ValueError(f"Unknown {self._primitive} provider '{resolved}'. Available: {available}")
        return provider


class ProviderRegistry:
    def __init__(self) -> None:
        self._primitives: dict[str, _PrimitiveProviders] = {}

    def initialize(self, app_settings: Settings | None = None) -> None:
        """Load and instantiate all providers from configuration."""
        cfg = (app_settings or settings).providers

        for primitive in PRIMITIVES:
            prim_cfg: PrimitiveProvidersConfig = getattr(cfg, primitive)
            expected_type = _EXPECTED_TYPES[primitive]

            providers: dict[str, Any] = {}
            for name, backend_cfg in prim_cfg.backends.items():
                logger.info(
                    "Loading %s provider '%s': %s",
                    primitive,
                    name,
                    backend_cfg.backend,
                )
                cls = _load_class(backend_cfg.backend)
                instance = cls(**backend_cfg.config)
                if not isinstance(instance, expected_type):
                    raise TypeError(f"Provider {backend_cfg.backend} is not an instance of {expected_type.__name__}")
                providers[name] = MetricsProxy(instance, primitive, name)

            self._primitives[primitive] = _PrimitiveProviders(
                primitive=primitive,
                default_name=prim_cfg.default,
                providers=providers,
            )

        logger.info("All providers initialized")

    async def reload(self, new_settings: Settings) -> None:
        """Hot-reload providers from new settings.

        Three-phase approach:
        1. Build all new providers (if any fails, old providers stay).
        2. Swap ``self._primitives`` atomically (safe under GIL).
        3. Close old providers that expose a ``close()`` method.
        """
        cfg = new_settings.providers
        new_primitives: dict[str, _PrimitiveProviders] = {}

        for primitive in PRIMITIVES:
            prim_cfg: PrimitiveProvidersConfig = getattr(cfg, primitive)
            expected_type = _EXPECTED_TYPES[primitive]

            providers: dict[str, Any] = {}
            for name, backend_cfg in prim_cfg.backends.items():
                logger.info(
                    "Reload: loading %s provider '%s': %s",
                    primitive,
                    name,
                    backend_cfg.backend,
                )
                cls = _load_class(backend_cfg.backend)
                instance = cls(**backend_cfg.config)
                if not isinstance(instance, expected_type):
                    raise TypeError(f"Provider {backend_cfg.backend} is not an instance of {expected_type.__name__}")
                providers[name] = MetricsProxy(instance, primitive, name)

            new_primitives[primitive] = _PrimitiveProviders(
                primitive=primitive,
                default_name=prim_cfg.default,
                providers=providers,
            )

        old_primitives = self._primitives
        self._primitives = new_primitives
        logger.info("Provider swap complete")

        await self._close_primitives(old_primitives)

    @staticmethod
    async def _close_primitives(primitives: dict[str, _PrimitiveProviders]) -> None:
        """Best-effort close on old providers that expose a ``close()`` method."""
        for prim_providers in primitives.values():
            for name in prim_providers.names:
                provider = prim_providers.get(name)
                # MetricsProxy delegates attribute access to the underlying provider
                close_fn = getattr(provider, "close", None)
                if close_fn is not None and callable(close_fn):
                    try:
                        result = close_fn()
                        # Support both sync and async close()
                        if hasattr(result, "__await__"):
                            await result
                        logger.info("Closed old provider: %s/%s", prim_providers._primitive, name)
                    except Exception:
                        logger.exception("Error closing provider %s/%s", prim_providers._primitive, name)

    def get_primitive(self, primitive: str) -> _PrimitiveProviders:
        if primitive not in self._primitives:
            raise RuntimeError(f"Provider registry not initialized or unknown primitive: {primitive}")
        return self._primitives[primitive]

    def list_providers(self) -> dict[str, dict[str, Any]]:
        """Return provider info for all primitives (used by discovery endpoint)."""
        result = {}
        for primitive, prim_providers in self._primitives.items():
            result[primitive] = {
                "default": prim_providers.default_name,
                "available": prim_providers.names,
            }
        return result

    # ── Convenience properties (context-aware) ──────────────────────

    @property
    def memory(self) -> MemoryProvider:
        provider: MemoryProvider = self.get_primitive(Primitive.MEMORY).get()
        return provider

    @property
    def observability(self) -> ObservabilityProvider:
        provider: ObservabilityProvider = self.get_primitive(Primitive.OBSERVABILITY).get()
        return provider

    @property
    def gateway(self) -> GatewayProvider:
        provider: GatewayProvider = self.get_primitive(Primitive.GATEWAY).get()
        return provider

    @property
    def tools(self) -> ToolsProvider:
        provider: ToolsProvider = self.get_primitive(Primitive.TOOLS).get()
        return provider

    @property
    def identity(self) -> IdentityProvider:
        provider: IdentityProvider = self.get_primitive(Primitive.IDENTITY).get()
        return provider

    @property
    def code_interpreter(self) -> CodeInterpreterProvider:
        provider: CodeInterpreterProvider = self.get_primitive(Primitive.CODE_INTERPRETER).get()
        return provider

    @property
    def browser(self) -> BrowserProvider:
        provider: BrowserProvider = self.get_primitive(Primitive.BROWSER).get()
        return provider

    @property
    def policy(self) -> PolicyProvider:
        provider: PolicyProvider = self.get_primitive(Primitive.POLICY).get()
        return provider

    @property
    def evaluations(self) -> EvaluationsProvider:
        provider: EvaluationsProvider = self.get_primitive(Primitive.EVALUATIONS).get()
        return provider


registry = ProviderRegistry()
