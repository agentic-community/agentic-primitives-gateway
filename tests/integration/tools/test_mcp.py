"""Integration tests for the MCP Registry tools primitive.

Full stack with real MCP Registry calls:
AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
MCPRegistryProvider -> real MCP Gateway Registry.

Requires:
  - A running MCP Gateway Registry instance
  - MCP_REGISTRY_URL env var (e.g. http://localhost:8080)
  - Optionally MCP_REGISTRY_TOKEN (JWT token for auth)
"""

from __future__ import annotations

import contextlib
import os
from uuid import uuid4

import httpx
import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration


# -- Skip logic ---------------------------------------------------------------

if not os.environ.get("MCP_REGISTRY_URL"):
    pytest.skip(
        "MCP_REGISTRY_URL not set -- skipping MCP Registry integration tests",
        allow_module_level=True,
    )

FAKE_AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
FAKE_AWS_REGION = "us-east-1"


# -- Registry initialization --------------------------------------------------


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with MCP Registry tools provider (noop for everything else).

    MCP Registry credentials are read from env vars and baked into the provider
    config so the provider does not need per-request credential headers.
    """
    base_url = os.environ["MCP_REGISTRY_URL"]
    token = os.environ.get("MCP_REGISTRY_TOKEN", "")

    test_settings = Settings(
        allow_server_credentials="always",
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.noop.NoopMemoryProvider",
                "config": {},
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "llm": {
                "backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider",
                "config": {},
            },
            "tools": {
                "backend": "agentic_primitives_gateway.primitives.tools.mcp_registry.MCPRegistryProvider",
                "config": {
                    "base_url": base_url,
                    "token": token or None,
                    "verify_ssl": False,
                },
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
        },
    )
    orig_settings = _config_module.settings
    _config_module.settings = test_settings
    registry.initialize(test_settings)

    yield

    _config_module.settings = orig_settings


# -- Client fixture ------------------------------------------------------------


@pytest.fixture
async def client():
    """AgenticPlatformClient wired to ASGI app with fake AWS creds.

    MCP Registry does not need AWS credentials -- they are baked into the
    provider config. We use fake AWS creds to satisfy the middleware.
    """
    transport = httpx.ASGITransport(app=app)
    async with AgenticPlatformClient(
        base_url="http://testserver",
        aws_access_key_id=FAKE_AWS_ACCESS_KEY,
        aws_secret_access_key=FAKE_AWS_SECRET_KEY,
        aws_region=FAKE_AWS_REGION,
        max_retries=2,
        transport=transport,
    ) as c:
        yield c


# -- Helpers -------------------------------------------------------------------

_TEST_PREFIX = "integ-mcp-test"


def _unique_server_name() -> str:
    return f"{_TEST_PREFIX}-{uuid4().hex[:8]}"


# -- List tools ----------------------------------------------------------------


class TestListTools:
    async def test_list_tools(self, client: AgenticPlatformClient) -> None:
        result = await client.list_tools()

        assert "tools" in result
        assert isinstance(result["tools"], list)

    async def test_list_tools_shape(self, client: AgenticPlatformClient) -> None:
        result = await client.list_tools()

        for tool in result["tools"]:
            assert "name" in tool
            assert "description" in tool


# -- Search tools --------------------------------------------------------------


class TestSearchTools:
    async def test_search_tools(self, client: AgenticPlatformClient) -> None:
        result = await client.search_tools("test")

        assert "tools" in result
        assert isinstance(result["tools"], list)

    async def test_search_tools_max_results(self, client: AgenticPlatformClient) -> None:
        result = await client.search_tools("test", max_results=2)

        assert "tools" in result
        assert len(result["tools"]) <= 2


# -- Register tool (via server registration) -----------------------------------


class TestRegisterTool:
    @pytest.mark.skip(reason="MCP Registry discovers tools from servers — individual tool registration not supported")
    async def test_register_tool(self, client: AgenticPlatformClient) -> None:
        """Register a tool definition via the tools register endpoint."""
        tool_def = {
            "name": f"{_TEST_PREFIX}-tool-{uuid4().hex[:8]}",
            "description": "Integration test tool",
            "parameters": {"type": "object", "properties": {"input": {"type": "string"}}},
        }

        result = await client.register_tool(tool_def)

        assert result["name"] == tool_def["name"]
        assert result["description"] == tool_def["description"]


# -- Get tool ------------------------------------------------------------------


class TestGetTool:
    async def test_get_tool(self, client: AgenticPlatformClient) -> None:
        """List tools, then retrieve one by name."""
        listed = await client.list_tools()
        tools = listed["tools"]
        if not tools:
            pytest.skip("No tools available in registry to test get_tool")

        tool_name = tools[0]["name"]
        result = await client.get_tool(tool_name)

        assert result["name"] == tool_name
        assert "description" in result

    async def test_get_tool_not_found(self, client: AgenticPlatformClient) -> None:
        """Requesting a non-existent tool returns 404."""
        from agentic_primitives_gateway_client import AgenticPlatformError

        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.get_tool("nonexistent-server/nonexistent-tool-xyz")
        assert exc_info.value.status_code == 404


# -- Invoke tool ---------------------------------------------------------------


class TestInvokeTool:
    async def test_invoke_tool(self, client: AgenticPlatformClient) -> None:
        """Invoke a tool from the registry and verify response shape."""
        listed = await client.list_tools()
        tools = listed["tools"]
        if not tools:
            pytest.skip("No tools available in registry to test invoke_tool")

        tool = tools[0]
        tool_name = tool["name"]

        result = await client.invoke_tool(tool_name, {})

        assert result["tool_name"] == tool_name
        assert "result" in result or "error" in result


# -- Delete tool ---------------------------------------------------------------


class TestDeleteTool:
    @pytest.mark.skip(reason="MCP Registry discovers tools from servers — individual tool deletion not supported")
    async def test_delete_tool(self, client: AgenticPlatformClient) -> None:
        """Register a tool, then delete it."""
        tool_name = f"{_TEST_PREFIX}-del-{uuid4().hex[:8]}"
        tool_def = {
            "name": tool_name,
            "description": "Temporary tool for delete test",
            "parameters": {},
        }

        await client.register_tool(tool_def)

        # Delete should not raise
        await client.delete_tool(tool_name)

    async def test_delete_tool_not_found(self, client: AgenticPlatformClient) -> None:
        """Deleting a non-existent tool returns an error."""
        with pytest.raises(httpx.HTTPStatusError):
            await client.delete_tool("nonexistent-tool-for-delete-test")


# -- List servers --------------------------------------------------------------


class TestListServers:
    async def test_list_servers(self, client: AgenticPlatformClient) -> None:
        result = await client.list_tool_servers()

        assert "servers" in result
        assert isinstance(result["servers"], list)

    async def test_list_servers_shape(self, client: AgenticPlatformClient) -> None:
        result = await client.list_tool_servers()

        for server in result["servers"]:
            assert "name" in server
            assert "health_status" in server
            assert "tools_count" in server


# -- Cleanup registered test tools --------------------------------------------


@pytest.fixture(autouse=True)
async def _cleanup_test_tools(client: AgenticPlatformClient):
    """Clean up any tools registered during tests.

    Runs after each test. Finds tools whose names start with the test prefix
    and deletes them.
    """
    yield

    with contextlib.suppress(Exception):
        listed = await client.list_tools()
        for tool in listed.get("tools", []):
            name = tool.get("name", "")
            if _TEST_PREFIX in name:
                with contextlib.suppress(Exception):
                    await client.delete_tool(name)
