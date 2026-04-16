"""Integration tests for the policy primitive.

Full stack with real AWS calls:
AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
AgentCorePolicyProvider -> real AWS Bedrock AgentCore services.

Requires:
  - Valid AWS credentials (via environment or profile)
"""

from __future__ import annotations

import asyncio
import contextlib
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def policy_engine(client):
    """Create a policy engine, wait for ACTIVE, yield ID, delete on teardown."""
    name = f"integ_engine_{uuid4().hex[:8]}"
    result = await client.create_policy_engine(name=name, description="integration test")
    engine_id = result["policy_engine_id"]
    # Wait for the engine to become ACTIVE
    for _ in range(60):
        info = await client.get_policy_engine(engine_id)
        if info.get("status", "").upper() == "ACTIVE":
            break
        await asyncio.sleep(1)
    yield engine_id
    with contextlib.suppress(Exception):
        await client.delete_policy_engine(engine_id)


class TestPolicyEngineLifecycle:
    @pytest.mark.asyncio
    async def test_create_and_get(self, client, policy_engine):
        info = await client.get_policy_engine(policy_engine)
        assert info["policy_engine_id"] == policy_engine

    @pytest.mark.asyncio
    async def test_list_engines(self, client, policy_engine):
        result = await client.list_policy_engines()
        ids = [e["policy_engine_id"] for e in result.get("policy_engines", [])]
        assert policy_engine in ids

    @pytest.mark.asyncio
    async def test_delete_engine(self, client):
        name = f"integ_del_{uuid4().hex[:8]}"
        result = await client.create_policy_engine(name=name)
        engine_id = result["policy_engine_id"]
        # Wait for ACTIVE before deleting
        for _ in range(60):
            info = await client.get_policy_engine(engine_id)
            if info.get("status", "").upper() == "ACTIVE":
                break
            await asyncio.sleep(1)
        await client.delete_policy_engine(engine_id)


class TestPolicyCRUD:
    @pytest.mark.asyncio
    async def test_policy_lifecycle(self, client, policy_engine):
        # Create
        cedar = "permit(principal, action, resource is AgentCore::Gateway);"
        result = await client.create_policy(policy_engine, policy_body=cedar)
        policy_id = result["policy_id"]

        # Wait for policy to become ACTIVE
        for _ in range(30):
            info = await client.get_policy(policy_engine, policy_id)
            if info.get("status", "").upper() == "ACTIVE":
                break
            await asyncio.sleep(1)

        # Get
        assert info["policy_id"] == policy_id

        # List
        listed = await client.list_policies(policy_engine)
        ids = [p["policy_id"] for p in listed.get("policies", [])]
        assert policy_id in ids

        # Delete
        await client.delete_policy(policy_engine, policy_id)
