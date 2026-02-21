"""Prometheus metrics for the Agentic Primitives Gateway.

All metrics include ``primitive`` and ``provider`` labels so operators can
filter and aggregate by either dimension in a multi-provider setup.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)

# ── Counters ────────────────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "agentic_primitives_gateway_requests_total",
    "Total requests handled by provider methods",
    ["primitive", "provider", "method", "status"],
)

ERROR_COUNT = Counter(
    "agentic_primitives_gateway_provider_errors_total",
    "Total errors raised by provider methods",
    ["primitive", "provider", "method", "error_type"],
)

# ── Histograms ──────────────────────────────────────────────────────

REQUEST_DURATION = Histogram(
    "agentic_primitives_gateway_request_duration_seconds",
    "Latency of provider method calls in seconds",
    ["primitive", "provider", "method"],
)

# ── Gauges ──────────────────────────────────────────────────────────

ACTIVE_SESSIONS = Gauge(
    "agentic_primitives_gateway_active_sessions",
    "Number of active sessions (browser / code_interpreter)",
    ["primitive", "provider"],
)

PROVIDER_HEALTH = Gauge(
    "agentic_primitives_gateway_provider_health",
    "Provider health status (1=healthy, 0=unhealthy)",
    ["primitive", "provider"],
)

# Methods that represent session lifecycle transitions.
_SESSION_START_METHODS = frozenset({"start_session"})
_SESSION_STOP_METHODS = frozenset({"stop_session"})


class MetricsProxy:
    """Transparent proxy that records Prometheus metrics on every method call.

    Wraps a provider instance so that all public async methods are
    automatically instrumented with request counts, error counts, latency
    histograms, and (for session-bearing primitives) active-session gauges.
    """

    def __init__(self, provider: Any, primitive: str, provider_name: str) -> None:
        self._provider = provider
        self._primitive = primitive
        self._provider_name = provider_name

    # Forward attribute access (properties, sync helpers, etc.) to the real
    # provider so that ``isinstance`` checks done *after* wrapping still work
    # for duck-typed access patterns.
    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._provider, name)
        if callable(attr) and asyncio.iscoroutinefunction(attr) and not name.startswith("_"):
            return self._wrap_async(name, attr)
        return attr

    # Allow ``isinstance(proxy, SomeProviderBase)`` to succeed.
    def __isinstance_check__(self, cls: type) -> bool:  # pragma: no cover
        return isinstance(self._provider, cls)

    def _wrap_async(self, method_name: str, func: Any) -> Any:
        primitive = self._primitive
        provider_name = self._provider_name

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            with REQUEST_DURATION.labels(
                primitive=primitive,
                provider=provider_name,
                method=method_name,
            ).time():
                try:
                    result = await func(*args, **kwargs)
                    REQUEST_COUNT.labels(
                        primitive=primitive,
                        provider=provider_name,
                        method=method_name,
                        status="success",
                    ).inc()

                    # Track active sessions for browser / code_interpreter.
                    if method_name in _SESSION_START_METHODS:
                        ACTIVE_SESSIONS.labels(
                            primitive=primitive,
                            provider=provider_name,
                        ).inc()
                    elif method_name in _SESSION_STOP_METHODS:
                        ACTIVE_SESSIONS.labels(
                            primitive=primitive,
                            provider=provider_name,
                        ).dec()

                    return result
                except Exception as exc:
                    REQUEST_COUNT.labels(
                        primitive=primitive,
                        provider=provider_name,
                        method=method_name,
                        status="error",
                    ).inc()
                    ERROR_COUNT.labels(
                        primitive=primitive,
                        provider=provider_name,
                        method=method_name,
                        error_type=type(exc).__name__,
                    ).inc()
                    raise

        return wrapper
