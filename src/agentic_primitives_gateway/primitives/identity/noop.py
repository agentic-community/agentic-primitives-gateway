from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.models.enums import TokenType
from agentic_primitives_gateway.primitives.identity.base import IdentityProvider

logger = logging.getLogger(__name__)


class NoopIdentityProvider(IdentityProvider):
    """No-op identity provider that returns placeholder values."""

    def __init__(self, **kwargs: Any) -> None:
        logger.info("NoopIdentityProvider initialized")

    async def get_token(
        self,
        provider_name: str,
        scopes: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.debug("noop get_token: %s", provider_name)
        return {
            "access_token": "",
            "token_type": TokenType.BEARER,
            "scopes": scopes or [],
        }

    async def get_api_key(
        self,
        provider_name: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.debug("noop get_api_key: %s", provider_name)
        return {"api_key": "", "provider_name": provider_name}

    async def list_providers(self) -> list[dict[str, Any]]:
        logger.debug("noop list_providers")
        return []
