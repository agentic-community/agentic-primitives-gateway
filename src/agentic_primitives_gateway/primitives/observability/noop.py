from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.primitives.observability.base import ObservabilityProvider

logger = logging.getLogger(__name__)


class NoopObservabilityProvider(ObservabilityProvider):
    """No-op observability provider that logs calls but does nothing."""

    def __init__(self, **kwargs: Any) -> None:
        logger.info("NoopObservabilityProvider initialized")

    async def ingest_trace(self, trace: dict[str, Any]) -> None:
        logger.debug("noop ingest_trace: %s", trace)

    async def ingest_log(self, log_entry: dict[str, Any]) -> None:
        logger.debug("noop ingest_log: %s", log_entry)

    async def query_traces(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        logger.debug("noop query_traces: %s", filters)
        return []
