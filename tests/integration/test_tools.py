"""Integration tests for the AgentCore tools primitive.

Full stack with real AWS calls:
AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreGatewayProvider → real MCP gateway (JSON-RPC).

Requires: AWS credentials + AGENTCORE_GATEWAY_ID env var pointing to
a pre-provisioned gateway.
"""

from __future__ import annotations

import os

import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration

_gateway_id = os.environ.get("AGENTCORE_GATEWAY_ID")
_skip_no_gateway = pytest.mark.skipif(
    not _gateway_id,
    reason="AGENTCORE_GATEWAY_ID not set — skipping tools integration tests",
)


# ── List tools ───────────────────────────────────────────────────────


@_skip_no_gateway
class TestListTools:
    async def test_list_tools(self, client: AgenticPlatformClient) -> None:
        result = await client.list_tools()

        assert "tools" in result
        assert isinstance(result["tools"], list)
        # A provisioned gateway should expose at least one tool
        assert len(result["tools"]) >= 1

        # Each tool should have name and description
        for tool in result["tools"]:
            assert "name" in tool
            assert "description" in tool


# ── Get tool ─────────────────────────────────────────────────────────


@_skip_no_gateway
class TestGetTool:
    async def test_get_tool(self, client: AgenticPlatformClient) -> None:
        # First list to get a known tool name
        listed = await client.list_tools()
        assert len(listed["tools"]) >= 1

        tool_name = listed["tools"][0]["name"]

        result = await client.get_tool(tool_name)

        assert result["name"] == tool_name
        assert "description" in result


# ── Invoke tool ──────────────────────────────────────────────────────


@_skip_no_gateway
class TestInvokeTool:
    async def test_invoke_tool(self, client: AgenticPlatformClient) -> None:
        # List tools to find one we can invoke
        listed = await client.list_tools()
        assert len(listed["tools"]) >= 1

        tool = listed["tools"][0]
        tool_name = tool["name"]

        # Invoke with empty params (or minimal required params).
        # The result shape depends on the tool; we just verify the
        # response structure is valid.
        result = await client.invoke_tool(tool_name, {})

        assert result["tool_name"] == tool_name
        # Should have either "result" or "error"
        assert "result" in result or "error" in result
