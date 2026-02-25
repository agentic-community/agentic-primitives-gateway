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
        credential_provider: str,
        workload_token: str,
        *,
        auth_flow: str = "M2M",
        scopes: list[str] | None = None,
        callback_url: str | None = None,
        force_auth: bool = False,
        session_uri: str | None = None,
        custom_state: str | None = None,
        custom_parameters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        logger.debug("noop get_token: %s", credential_provider)
        return {
            "access_token": "",
            "token_type": TokenType.BEARER,
            "scopes": scopes or [],
        }

    async def get_api_key(
        self,
        credential_provider: str,
        workload_token: str,
    ) -> dict[str, Any]:
        logger.debug("noop get_api_key: %s", credential_provider)
        return {"api_key": "", "credential_provider": credential_provider}

    async def get_workload_token(
        self,
        workload_name: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        logger.debug("noop get_workload_token: %s", workload_name)
        return {"workload_token": "", "workload_name": workload_name}

    async def list_credential_providers(self) -> list[dict[str, Any]]:
        logger.debug("noop list_credential_providers")
        return []
