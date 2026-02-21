from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from bedrock_agentcore.services.identity import IdentityClient

from agentic_primitives_gateway.context import get_boto3_session
from agentic_primitives_gateway.models.enums import TokenType
from agentic_primitives_gateway.primitives.identity.base import IdentityProvider

logger = logging.getLogger(__name__)


class AgentCoreIdentityProvider(IdentityProvider):
    """Identity provider backed by AWS Bedrock AgentCore Identity service.

    AWS credentials are read from request context on every call. The caller's
    boto3 session is used to authenticate to the AgentCore Identity service.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.identity.agentcore.AgentCoreIdentityProvider
        config:
          region: "us-east-1"
    """

    def __init__(self, region: str = "us-east-1", **kwargs: Any) -> None:
        self._region = region
        logger.info("AgentCore identity provider initialized (region=%s)", region)

    def _get_client(self) -> IdentityClient:
        """Create an IdentityClient using the current request's boto3 session."""
        session = get_boto3_session(default_region=self._region)
        return IdentityClient(region=session.region_name)

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def get_token(
        self,
        provider_name: str,
        scopes: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = context or {}
        agent_identity_token = ctx.get("agent_identity_token", "")
        client = self._get_client()

        token = await self._run_sync(
            client.get_token,
            provider_name=provider_name,
            agent_identity_token=agent_identity_token,
            scopes=scopes,
        )
        return {"access_token": token, "token_type": TokenType.BEARER}

    async def get_api_key(
        self,
        provider_name: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ctx = context or {}
        agent_identity_token = ctx.get("agent_identity_token", "")
        client = self._get_client()

        key = await self._run_sync(
            client.get_api_key,
            provider_name=provider_name,
            agent_identity_token=agent_identity_token,
        )
        return {"api_key": key, "provider_name": provider_name}

    async def list_providers(self) -> list[dict[str, Any]]:
        return []

    async def healthcheck(self) -> bool:
        return True
