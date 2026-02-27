"""Shared fixtures for real AWS integration tests.

These tests exercise the full stack without mocks:
AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCore provider → real AWS Bedrock AgentCore services.

Tests are skipped automatically when AWS credentials are not available.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from uuid import uuid4

import httpx
import pytest

from agentic_primitives_gateway.config import Settings, settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration


# ── Skip logic ────────────────────────────────────────────────────────


def _has_aws_credentials() -> bool:
    """Check if AWS credentials are available via boto3."""
    try:
        import boto3

        sts = boto3.client("sts")
        sts.get_caller_identity()
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True, scope="session")
def _skip_without_credentials():
    if not _has_aws_credentials():
        pytest.skip("AWS credentials not available — skipping integration tests")


# ── Registry initialization ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with real AgentCore providers.

    Unlike system tests, no SDK methods are mocked.  The observability
    provider's ``__init__`` calls ``_ensure_log_group`` and ``_setup_tracer``
    with real server-side credentials (``allow_server_credentials=True``).
    """
    region = os.environ.get("AWS_REGION", "us-east-1")
    gateway_id = os.environ.get("AGENTCORE_GATEWAY_ID")

    tools_config: dict = {"region": region}
    if gateway_id:
        tools_config["gateway_id"] = gateway_id

    test_settings = Settings(
        allow_server_credentials=True,
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider",
                "config": {"region": region},
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.agentcore.AgentCoreObservabilityProvider",
                "config": {
                    "region": region,
                    "service_name": "integ-test",
                    "agent_id": "integ-agent",
                },
            },
            "gateway": {
                "backend": "agentic_primitives_gateway.primitives.gateway.noop.NoopGatewayProvider",
                "config": {},
            },
            "tools": {
                "backend": "agentic_primitives_gateway.primitives.tools.agentcore.AgentCoreGatewayProvider",
                "config": tools_config,
            },
            "identity": {
                "backend": "agentic_primitives_gateway.primitives.identity.agentcore.AgentCoreIdentityProvider",
                "config": {"region": region},
            },
            "code_interpreter": {
                "backend": "agentic_primitives_gateway.primitives.code_interpreter.agentcore.AgentCoreCodeInterpreterProvider",
                "config": {"region": region},
            },
            "browser": {
                "backend": "agentic_primitives_gateway.primitives.browser.agentcore.AgentCoreBrowserProvider",
                "config": {"region": region},
            },
        },
    )
    # Patch the global settings singleton so that get_boto3_session() and
    # other helpers that check allow_server_credentials see the test value.
    settings.allow_server_credentials = True
    registry.initialize(test_settings)


# ── Client fixture ───────────────────────────────────────────────────


@pytest.fixture
async def client():
    """AgenticPlatformClient wired to the ASGI app with real AWS credentials."""
    transport = httpx.ASGITransport(app=app)
    async with AgenticPlatformClient(
        base_url="http://testserver",
        aws_from_environment=True,
        max_retries=2,
        transport=transport,
    ) as c:
        yield c


# ── Resource lifecycle fixtures ──────────────────────────────────────


@pytest.fixture
async def memory_resource(client: AgenticPlatformClient):
    """Resolve a memory resource for testing.

    Prefers AGENTCORE_MEMORY_ID env var.  Falls back to the first ACTIVE
    memory from the control plane.  Creates a new one only as a last resort
    (memory provisioning can take several minutes).
    """
    memory_id = os.environ.get("AGENTCORE_MEMORY_ID")
    created = False

    if not memory_id:
        # Try to reuse an existing ACTIVE memory resource
        listed = await client.list_memory_resources()
        for r in listed.get("resources", []):
            if r.get("status", "").upper() == "ACTIVE":
                memory_id = r["memory_id"]
                break

    if not memory_id:
        # Last resort: create one and wait
        name = f"integ_{uuid4().hex[:8]}"
        result = await client.create_memory_resource(name)
        memory_id = result["memory_id"]
        created = True
        for _ in range(90):
            info = await client.get_memory_resource(memory_id)
            if info.get("status", "").upper() == "ACTIVE":
                break
            await asyncio.sleep(2)
        else:
            pytest.fail(f"Memory resource {memory_id} did not become ACTIVE within 180s")

    client.set_service_credentials("agentcore", {"memory_id": memory_id})
    yield memory_id

    if created:
        with contextlib.suppress(Exception):
            await client.delete_memory_resource(memory_id)


@pytest.fixture
async def code_session(client: AgenticPlatformClient):
    """Start a code interpreter session, yield ID, stop on teardown."""
    result = await client.start_code_session()
    sid = result["session_id"]
    yield sid
    with contextlib.suppress(Exception):
        await client.stop_code_session(sid)


@pytest.fixture
async def browser_session(client: AgenticPlatformClient):
    """Start a browser session, yield ID, stop on teardown."""
    result = await client.start_browser_session()
    sid = result["session_id"]
    yield sid
    with contextlib.suppress(Exception):
        await client.stop_browser_session(sid)
