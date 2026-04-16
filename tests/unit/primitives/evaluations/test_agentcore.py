from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.evaluations.agentcore import (
    AgentCoreEvaluationsProvider,
)


@patch("agentic_primitives_gateway.primitives.evaluations.agentcore.get_boto3_session")
class TestAgentCoreEvaluationsProvider:
    """Tests for the AgentCore evaluations provider."""

    def _setup_mocks(self, mock_get_session):
        """Set up control and data client mocks with service-based routing."""
        mock_control_client = MagicMock()
        mock_data_client = MagicMock()
        mock_session = MagicMock()
        mock_session.region_name = "us-east-1"

        def _client_factory(service, **kwargs):
            if service == "bedrock-agentcore-control":
                return mock_control_client
            return mock_data_client

        mock_session.client.side_effect = _client_factory
        mock_get_session.return_value = mock_session
        return mock_control_client, mock_data_client

    # ── Evaluator tests ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_evaluator(self, mock_get_session):
        mock_control_client, _ = self._setup_mocks(mock_get_session)

        mock_control_client.create_evaluator.return_value = {
            "evaluatorId": "eval-1",
            "status": "CREATING",
        }

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        result = await provider.create_evaluator(
            name="test",
            evaluator_type="TRACE",
            config={"evaluatorConfig": {"llmAsAJudge": {}}, "level": "TRACE"},
        )

        assert result["evaluator_id"] == "eval-1"
        assert result["status"] == "CREATING"
        mock_control_client.create_evaluator.assert_called_once()
        call_kwargs = mock_control_client.create_evaluator.call_args[1]
        assert call_kwargs["evaluatorName"] == "test"
        assert call_kwargs["evaluatorConfig"] == {"llmAsAJudge": {}}
        assert call_kwargs["level"] == "TRACE"

    @pytest.mark.asyncio
    async def test_get_evaluator(self, mock_get_session):
        mock_control_client, _ = self._setup_mocks(mock_get_session)

        mock_control_client.get_evaluator.return_value = {
            "evaluatorId": "eval-1",
            "level": "TRACE",
        }

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        result = await provider.get_evaluator("eval-1")

        assert result["evaluator_id"] == "eval-1"
        assert result["level"] == "TRACE"
        mock_control_client.get_evaluator.assert_called_once_with(evaluatorId="eval-1")

    @pytest.mark.asyncio
    async def test_update_evaluator(self, mock_get_session):
        mock_control_client, _ = self._setup_mocks(mock_get_session)

        mock_control_client.update_evaluator.return_value = {
            "evaluatorId": "eval-1",
        }

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        result = await provider.update_evaluator("eval-1", description="updated desc")

        assert result["evaluator_id"] == "eval-1"
        mock_control_client.update_evaluator.assert_called_once_with(evaluatorId="eval-1", description="updated desc")

    @pytest.mark.asyncio
    async def test_delete_evaluator(self, mock_get_session):
        mock_control_client, _ = self._setup_mocks(mock_get_session)

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        await provider.delete_evaluator("eval-1")

        mock_control_client.delete_evaluator.assert_called_once_with(evaluatorId="eval-1")

    @pytest.mark.asyncio
    async def test_list_evaluators(self, mock_get_session):
        mock_control_client, _ = self._setup_mocks(mock_get_session)

        mock_control_client.list_evaluators.return_value = {
            "evaluators": [{"evaluatorId": "eval-1"}],
        }

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        result = await provider.list_evaluators()

        assert "evaluators" in result
        assert result["evaluators"][0]["evaluator_id"] == "eval-1"
        mock_control_client.list_evaluators.assert_called_once_with(maxResults=100)

    # ── Evaluate tests ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_evaluate_builtin(self, mock_get_session):
        _, mock_data_client = self._setup_mocks(mock_get_session)

        mock_data_client.evaluate.return_value = {
            "evaluationResults": [{"value": 0.85, "label": "GOOD", "evaluatorId": "eval-1"}],
        }

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        result = await provider.evaluate(
            evaluator_id="eval-1",
            input_data="session-span-data",
        )

        assert "evaluation_results" in result
        assert result["evaluation_results"][0]["value"] == 0.85
        mock_data_client.evaluate.assert_called_once_with(
            evaluatorId="eval-1",
            evaluationInput={"sessionSpans": ["session-span-data"]},
        )

    @pytest.mark.asyncio
    async def test_evaluate_with_target(self, mock_get_session):
        _, mock_data_client = self._setup_mocks(mock_get_session)

        mock_data_client.evaluate.return_value = {
            "evaluationResults": [{"value": 0.9, "label": "EXCELLENT"}],
        }

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        result = await provider.evaluate(
            evaluator_id="eval-1",
            target="trace-123",
            input_data="session-span-data",
        )

        assert "evaluation_results" in result
        assert result["evaluation_results"][0]["value"] == 0.9
        mock_data_client.evaluate.assert_called_once_with(
            evaluatorId="eval-1",
            evaluationInput={"sessionSpans": ["session-span-data"]},
            evaluationTarget={"traceIds": ["trace-123"]},
        )

    @pytest.mark.asyncio
    async def test_evaluate_custom(self, mock_get_session):
        _, mock_data_client = self._setup_mocks(mock_get_session)

        mock_data_client.evaluate.return_value = {
            "evaluationResults": [{"value": 0.75, "label": "ACCEPTABLE"}],
        }

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        result = await provider.evaluate(
            evaluator_id="custom-eval-1",
            input_data="span-data",
            output_data="span-id-1",
        )

        assert "evaluation_results" in result
        mock_data_client.evaluate.assert_called_once_with(
            evaluatorId="custom-eval-1",
            evaluationInput={"sessionSpans": ["span-data"]},
            evaluationTarget={"spanIds": ["span-id-1"]},
        )

    # ── Online eval config tests ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_online_eval_config(self, mock_get_session):
        mock_control_client, _ = self._setup_mocks(mock_get_session)

        mock_control_client.create_online_evaluation_config.return_value = {
            "onlineEvaluationConfigId": "cfg-1",
        }

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        result = await provider.create_online_evaluation_config(
            name="my-config",
            evaluator_ids=["eval-1", "eval-2"],
            config={
                "rule": {"ruleType": "ALL"},
                "dataSourceConfig": {"dataSourceType": "TRACE"},
                "evaluationExecutionRoleArn": "arn:aws:iam::123:role/test",
                "enableOnCreate": False,
            },
        )

        assert result["online_evaluation_config_id"] == "cfg-1"
        mock_control_client.create_online_evaluation_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_online_eval_config(self, mock_get_session):
        mock_control_client, _ = self._setup_mocks(mock_get_session)

        mock_control_client.get_online_evaluation_config.return_value = {
            "onlineEvaluationConfigId": "cfg-1",
        }

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        result = await provider.get_online_evaluation_config("cfg-1")

        assert result["online_evaluation_config_id"] == "cfg-1"
        mock_control_client.get_online_evaluation_config.assert_called_once_with(onlineEvaluationConfigId="cfg-1")

    @pytest.mark.asyncio
    async def test_delete_online_eval_config(self, mock_get_session):
        mock_control_client, _ = self._setup_mocks(mock_get_session)

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        await provider.delete_online_evaluation_config("cfg-1")

        mock_control_client.delete_online_evaluation_config.assert_called_once_with(onlineEvaluationConfigId="cfg-1")

    @pytest.mark.asyncio
    async def test_list_online_eval_configs(self, mock_get_session):
        mock_control_client, _ = self._setup_mocks(mock_get_session)

        mock_control_client.list_online_evaluation_configs.return_value = {
            "onlineEvaluationConfigs": [
                {"onlineEvaluationConfigId": "cfg-1"},
                {"onlineEvaluationConfigId": "cfg-2"},
            ],
        }

        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        result = await provider.list_online_evaluation_configs()

        assert "online_evaluation_configs" in result
        assert len(result["online_evaluation_configs"]) == 2
        assert result["online_evaluation_configs"][0]["online_evaluation_config_id"] == "cfg-1"
        mock_control_client.list_online_evaluation_configs.assert_called_once_with(maxResults=100)

    # ── Other tests ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_healthcheck(self, mock_get_session):
        provider = AgentCoreEvaluationsProvider(region="us-east-1")
        result = await provider.healthcheck()

        assert result is True
