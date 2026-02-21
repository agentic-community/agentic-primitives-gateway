from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from agentic_primitives_gateway.context import get_service_credentials
from agentic_primitives_gateway.primitives.tools.base import ToolsProvider

logger = logging.getLogger(__name__)


class AgentCoreGatewayProvider(ToolsProvider):
    """Tools provider backed by AWS Bedrock AgentCore Gateway.

    AgentCore Gateway exposes MCP-compatible tools from APIs, Lambda functions,
    and other services. This provider connects to a gateway endpoint via the
    MCP protocol to list and invoke tools.

    The gateway_url and access token can come from:
    1. Client headers: X-Cred-Agentcore-Gateway-Url, X-Cred-Agentcore-Gateway-Token
    2. Provider config: gateway_url, gateway_id
    3. Server credentials (if allow_server_credentials is enabled)

    Provider config example::

        backend: agentic_primitives_gateway.primitives.tools.agentcore.AgentCoreGatewayProvider
        config:
          region: "us-east-1"
          gateway_id: "your-gateway-id"
          gateway_url: "https://gateway-id.gateway.bedrock-agentcore.region.amazonaws.com/mcp"
    """

    def __init__(
        self,
        region: str = "us-east-1",
        gateway_id: str | None = None,
        gateway_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._region = region
        self._default_gateway_id = gateway_id
        self._default_gateway_url = gateway_url
        logger.info(
            "AgentCore Gateway tools provider initialized (region=%s, gateway_id=%s)",
            region,
            gateway_id or "(from client)",
        )

    def _resolve_gateway_url(self) -> str:
        """Resolve the gateway URL from context or config."""
        creds = get_service_credentials("agentcore")
        url = (creds or {}).get("gateway_url") or self._default_gateway_url
        if url:
            return url

        gateway_id = (creds or {}).get("gateway_id") or self._default_gateway_id
        if gateway_id:
            return f"https://{gateway_id}.gateway.bedrock-agentcore.{self._region}.amazonaws.com/mcp"

        raise ValueError(
            "AgentCore Gateway URL is required. Provide it via: "
            "(1) client header X-Cred-Agentcore-Gateway-Url, "
            "(2) gateway_url or gateway_id in the server provider config."
        )

    def _resolve_access_token(self) -> str | None:
        """Resolve access token from context."""
        creds = get_service_credentials("agentcore")
        return (creds or {}).get("gateway_token")

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def list_tools(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        gateway_url = self._resolve_gateway_url()
        access_token = self._resolve_access_token()

        def _list() -> list[dict[str, Any]]:
            import httpx

            headers: dict[str, str] = {"Content-Type": "application/json"}
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"

            # MCP list tools via JSON-RPC
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }

            with httpx.Client(timeout=30) as client:
                resp = client.post(gateway_url, json=payload, headers=headers)
                resp.raise_for_status()
                result = resp.json()

            tools_data = result.get("result", {}).get("tools", [])
            return [
                {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("inputSchema", {}),
                    "metadata": {k: v for k, v in t.items() if k not in ("name", "description", "inputSchema")},
                }
                for t in tools_data
            ]

        result: list[dict[str, Any]] = await self._run_sync(_list)
        return result

    async def invoke_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        gateway_url = self._resolve_gateway_url()
        access_token = self._resolve_access_token()

        def _invoke() -> dict[str, Any]:
            import httpx

            headers: dict[str, str] = {"Content-Type": "application/json"}
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"

            # MCP tools/call via JSON-RPC
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": params,
                },
            }

            with httpx.Client(timeout=60) as client:
                resp = client.post(gateway_url, json=payload, headers=headers)
                resp.raise_for_status()
                result = resp.json()

            if "error" in result:
                return {"error": result["error"].get("message", str(result["error"]))}

            content = result.get("result", {}).get("content", [])
            # Extract text from content blocks
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("text"):
                    text_parts.append(block["text"])
                elif isinstance(block, str):
                    text_parts.append(block)

            return {
                "result": "\n".join(text_parts) if text_parts else result.get("result"),
            }

        result: dict[str, Any] = await self._run_sync(_invoke)
        return result

    async def register_tool(self, tool_def: dict[str, Any]) -> None:
        # AgentCore Gateway tools are registered via the control plane (console/boto3),
        # not through the MCP data plane. Log a warning.
        logger.warning(
            "register_tool is not supported via the AgentCore Gateway MCP endpoint. "
            "Use the AWS console or boto3 bedrock-agentcore-control API to add gateway targets."
        )

    async def search_tools(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        # AgentCore Gateway supports semantic search natively
        # For now, fall back to list + filter; can be enhanced with the gateway's
        # semantic search API when available
        result: list[dict[str, Any]] = await super().search_tools(query, max_results)
        return result

    async def healthcheck(self) -> bool:
        try:
            await self.list_tools()
            return True
        except Exception:
            logger.debug("AgentCore Gateway healthcheck failed", exc_info=True)
            return False
