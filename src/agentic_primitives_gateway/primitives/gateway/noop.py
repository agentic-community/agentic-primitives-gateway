from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.primitives.gateway.base import GatewayProvider

logger = logging.getLogger(__name__)


class NoopGatewayProvider(GatewayProvider):
    """No-op gateway provider that returns placeholder values."""

    def __init__(self, **kwargs: Any) -> None:
        logger.info("NoopGatewayProvider initialized")

    async def route_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        logger.debug("noop route_request: %s", model_request)
        return {
            "model": model_request.get("model", ""),
            "content": "",
            "usage": {},
        }

    async def list_models(self) -> list[dict[str, Any]]:
        logger.debug("noop list_models")
        return []
