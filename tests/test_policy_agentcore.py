from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.policy.agentcore import (
    AgentCorePolicyProvider,
)


@patch("agentic_primitives_gateway.primitives.policy.agentcore.get_boto3_session")
class TestAgentCorePolicyProvider:
    """Tests for the AgentCore policy provider."""

    # ── Engine tests ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_policy_engine(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.create_policy_engine.return_value = {
            "policyEngineId": "eng-1",
            "name": "test-engine",
            "status": "CREATING",
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.create_policy_engine(name="test-engine", description="desc")

        assert result["policy_engine_id"] == "eng-1"
        assert result["name"] == "test-engine"
        assert result["status"] == "CREATING"
        mock_client.create_policy_engine.assert_called_once_with(name="test-engine", description="desc")

    @pytest.mark.asyncio
    async def test_get_policy_engine(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.get_policy_engine.return_value = {
            "policyEngineId": "eng-1",
            "name": "test",
            "status": "ACTIVE",
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.get_policy_engine("eng-1")

        assert result["policy_engine_id"] == "eng-1"
        assert result["name"] == "test"
        assert result["status"] == "ACTIVE"
        mock_client.get_policy_engine.assert_called_once_with(policyEngineId="eng-1")

    @pytest.mark.asyncio
    async def test_delete_policy_engine(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.delete_policy_engine.return_value = {}

        provider = AgentCorePolicyProvider(region="us-east-1")
        await provider.delete_policy_engine("eng-1")

        mock_client.delete_policy_engine.assert_called_once_with(policyEngineId="eng-1")

    @pytest.mark.asyncio
    async def test_list_policy_engines(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.list_policy_engines.return_value = {
            "policyEngines": [{"policyEngineId": "eng-1", "name": "test"}],
            "nextToken": None,
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.list_policy_engines()

        assert "policy_engines" in result
        assert len(result["policy_engines"]) == 1
        assert result["policy_engines"][0]["policy_engine_id"] == "eng-1"
        mock_client.list_policy_engines.assert_called_once_with(maxResults=100)

    @pytest.mark.asyncio
    async def test_list_policy_engines_with_pagination(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.list_policy_engines.return_value = {
            "policyEngines": [{"policyEngineId": "eng-1"}],
            "nextToken": "tok-2",
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.list_policy_engines(next_token="tok-1")

        assert result["next_token"] == "tok-2"
        mock_client.list_policy_engines.assert_called_once_with(maxResults=100, nextToken="tok-1")

    # ── Policy tests ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_policy(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.create_policy.return_value = {
            "policyId": "pol-1",
            "policyEngineId": "eng-1",
            "definition": "permit(principal, action, resource);",
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.create_policy(engine_id="eng-1", policy_body="permit(principal, action, resource);")

        assert result["policy_id"] == "pol-1"
        assert result["policy_engine_id"] == "eng-1"
        mock_client.create_policy.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_policy(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.get_policy.return_value = {
            "policyId": "pol-1",
            "definition": "permit(...);",
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.get_policy("eng-1", "pol-1")

        assert result["policy_id"] == "pol-1"
        assert result["definition"] == "permit(...);"
        mock_client.get_policy.assert_called_once_with(policyEngineId="eng-1", policyId="pol-1")

    @pytest.mark.asyncio
    async def test_update_policy(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.update_policy.return_value = {
            "policyId": "pol-1",
            "definition": "updated",
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.update_policy("eng-1", "pol-1", "updated")

        assert result["policy_id"] == "pol-1"
        assert result["definition"] == "updated"
        mock_client.update_policy.assert_called_once_with(
            policyEngineId="eng-1", policyId="pol-1", definition={"cedar": {"statement": "updated"}}
        )

    @pytest.mark.asyncio
    async def test_delete_policy(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.delete_policy.return_value = {}

        provider = AgentCorePolicyProvider(region="us-east-1")
        await provider.delete_policy("eng-1", "pol-1")

        mock_client.delete_policy.assert_called_once_with(policyEngineId="eng-1", policyId="pol-1")

    @pytest.mark.asyncio
    async def test_list_policies(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.list_policies.return_value = {
            "policies": [{"policyId": "pol-1"}],
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.list_policies("eng-1")

        assert "policies" in result
        assert result["policies"][0]["policy_id"] == "pol-1"
        mock_client.list_policies.assert_called_once_with(policyEngineId="eng-1", maxResults=100)

    # ── Generation tests ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_start_policy_generation(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.start_policy_generation.return_value = {
            "policyGenerationId": "gen-1",
            "status": "RUNNING",
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.start_policy_generation("eng-1")

        assert result["policy_generation_id"] == "gen-1"
        assert result["status"] == "RUNNING"
        mock_client.start_policy_generation.assert_called_once_with(policyEngineId="eng-1")

    @pytest.mark.asyncio
    async def test_get_policy_generation(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.get_policy_generation.return_value = {
            "policyGenerationId": "gen-1",
            "status": "COMPLETED",
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.get_policy_generation("eng-1", "gen-1")

        assert result["policy_generation_id"] == "gen-1"
        assert result["status"] == "COMPLETED"
        mock_client.get_policy_generation.assert_called_once_with(policyEngineId="eng-1", policyGenerationId="gen-1")

    @pytest.mark.asyncio
    async def test_list_policy_generations(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.list_policy_generations.return_value = {
            "policyGenerations": [
                {"policyGenerationId": "gen-1", "status": "COMPLETED"},
            ],
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.list_policy_generations("eng-1")

        assert "policy_generations" in result
        assert result["policy_generations"][0]["policy_generation_id"] == "gen-1"
        mock_client.list_policy_generations.assert_called_once_with(policyEngineId="eng-1", maxResults=100)

    @pytest.mark.asyncio
    async def test_list_policy_generation_assets(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.list_policy_generation_assets.return_value = {
            "policyGenerationAssets": [{"assetId": "asset-1", "type": "POLICY"}],
        }

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.list_policy_generation_assets("eng-1", "gen-1")

        assert "policy_generation_assets" in result
        assert result["policy_generation_assets"][0]["asset_id"] == "asset-1"
        mock_client.list_policy_generation_assets.assert_called_once_with(
            policyEngineId="eng-1", policyGenerationId="gen-1", maxResults=100
        )

    # ── Other tests ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_healthcheck(self, mock_get_session):
        mock_client = MagicMock()
        mock_session_obj = MagicMock()
        mock_session_obj.client.return_value = mock_client
        mock_session_obj.region_name = "us-east-1"
        mock_get_session.return_value = mock_session_obj

        mock_client.list_policy_engines.return_value = {"policyEngines": []}

        provider = AgentCorePolicyProvider(region="us-east-1")
        result = await provider.healthcheck()

        assert result is True
        mock_client.list_policy_engines.assert_called_once_with(maxResults=1)

    @pytest.mark.asyncio
    async def test_region_config(self, mock_get_session):
        provider = AgentCorePolicyProvider(region="us-west-2")
        assert provider._region == "us-west-2"
