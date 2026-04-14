from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.evaluations.langfuse import (
    LangfuseEvaluationsProvider,
    _score_config_to_dict,
    _score_to_dict,
    _to_data_type,
)


def _mock_score_config(
    id: str = "cfg-1",
    name: str = "helpfulness",
    data_type: str = "NUMERIC",
    is_archived: bool = False,
    description: str = "test",
    min_value: float | None = 0.0,
    max_value: float | None = 1.0,
) -> MagicMock:
    config = MagicMock()
    config.id = id
    config.name = name
    dt = MagicMock()
    dt.value = data_type
    config.data_type = dt
    config.is_archived = is_archived
    config.description = description
    config.min_value = min_value
    config.max_value = max_value
    config.categories = None
    config.created_at = datetime(2025, 1, 1, tzinfo=UTC)
    return config


def _mock_score(
    id: str = "score-1",
    name: str = "helpfulness",
    value: float = 0.9,
    trace_id: str | None = "trace-1",
    comment: str | None = None,
    data_type: str | None = "NUMERIC",
) -> MagicMock:
    score = MagicMock()
    score.id = id
    score.name = name
    score.value = value
    score.trace_id = trace_id
    score.observation_id = None
    score.comment = comment
    score.data_type = data_type
    return score


@patch("agentic_primitives_gateway.primitives.evaluations.langfuse.get_service_credentials_or_defaults")
class TestLangfuseEvaluationsProvider:
    def _make_provider(self) -> LangfuseEvaluationsProvider:
        return LangfuseEvaluationsProvider(
            public_key="pk-test",
            secret_key="sk-test",
            base_url="https://langfuse.test",
        )

    # ── Evaluator CRUD ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_evaluator(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_rest_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client
            client.score_configs.create.return_value = _mock_score_config()

            result = await provider.create_evaluator(
                name="helpfulness",
                evaluator_type="numeric",
                description="test",
            )

            assert result["evaluator_id"] == "cfg-1"
            assert result["name"] == "helpfulness"
            assert result["status"] == "ACTIVE"
            client.score_configs.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_evaluator(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_rest_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client
            client.score_configs.get_by_id.return_value = _mock_score_config(id="cfg-2", name="accuracy")

            result = await provider.get_evaluator("cfg-2")

            assert result["evaluator_id"] == "cfg-2"
            assert result["name"] == "accuracy"
            client.score_configs.get_by_id.assert_called_once_with(config_id="cfg-2")

    @pytest.mark.asyncio
    async def test_update_evaluator(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_rest_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client
            client.score_configs.update.return_value = _mock_score_config(description="updated")

            result = await provider.update_evaluator("cfg-1", description="updated")

            assert result["description"] == "updated"
            client.score_configs.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_evaluator_archives(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_rest_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client
            client.score_configs.update.return_value = _mock_score_config(is_archived=True)

            await provider.delete_evaluator("cfg-1")

            client.score_configs.update.assert_called_once_with(config_id="cfg-1", is_archived=True)

    @pytest.mark.asyncio
    async def test_list_evaluators(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_rest_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client
            mock_result = MagicMock()
            mock_result.data = [_mock_score_config(), _mock_score_config(id="cfg-2", name="accuracy")]
            client.score_configs.get.return_value = mock_result

            result = await provider.list_evaluators(max_results=10)

            assert len(result["evaluators"]) == 2
            assert result["evaluators"][0]["evaluator_id"] == "cfg-1"
            assert result["evaluators"][1]["evaluator_id"] == "cfg-2"

    # ── Evaluate ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_evaluate(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_sdk_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client

            result = await provider.evaluate(
                evaluator_id="helpfulness",
                target="trace-123",
                input_data="What is 2+2?",
                output_data="4",
                metadata={"value": 0.95},
            )

            assert result["evaluator_id"] == "helpfulness"
            assert result["results"][0]["value"] == 0.95
            assert result["results"][0]["trace_id"] == "trace-123"
            client.create_score.assert_called_once()
            client.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_default_value(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_sdk_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client

            result = await provider.evaluate(evaluator_id="test", output_data="good")

            assert result["results"][0]["value"] == 1.0

    # ── Score CRUD ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_create_score(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_rest_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client
            mock_response = MagicMock()
            mock_response.id = "score-1"
            client.legacy.score_v1.create.return_value = mock_response

            result = await provider.create_score(
                name="helpfulness",
                value=0.85,
                trace_id="trace-1",
                comment="Good response",
            )

            assert result["score_id"] == "score-1"
            assert result["name"] == "helpfulness"
            assert result["value"] == 0.85
            client.legacy.score_v1.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_score(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_rest_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client
            client.scores.get_by_id.return_value = _mock_score()

            result = await provider.get_score("score-1")

            assert result["score_id"] == "score-1"
            assert result["value"] == 0.9
            client.scores.get_by_id.assert_called_once_with(score_id="score-1")

    @pytest.mark.asyncio
    async def test_delete_score(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_rest_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client

            await provider.delete_score("score-1")

            client.legacy.score_v1.delete.assert_called_once_with(score_id="score-1")

    @pytest.mark.asyncio
    async def test_list_scores(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_rest_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client
            mock_result = MagicMock()
            mock_result.data = [_mock_score(), _mock_score(id="score-2", value=0.5)]
            mock_result.meta = MagicMock()
            mock_result.meta.total_items = 2
            client.scores.get_many.return_value = mock_result

            result = await provider.list_scores(trace_id="trace-1", limit=10)

            assert len(result["scores"]) == 2
            assert result["scores"][0]["score_id"] == "score-1"
            assert result["total_items"] == 2

    @pytest.mark.asyncio
    async def test_list_scores_with_filters(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = self._make_provider()

        with patch.object(provider, "_resolve_rest_client") as mock_rc:
            client = MagicMock()
            mock_rc.return_value = client
            mock_result = MagicMock()
            mock_result.data = []
            mock_result.meta = MagicMock()
            mock_result.meta.total_items = 0
            client.scores.get_many.return_value = mock_result

            result = await provider.list_scores(name="accuracy", data_type="numeric")

            assert result["scores"] == []
            call_kwargs = client.scores.get_many.call_args[1]
            assert call_kwargs["name"] == "accuracy"

    # ── Not supported ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_online_eval_configs_not_supported(self, mock_creds):
        mock_creds.return_value = {}
        provider = self._make_provider()

        with pytest.raises(NotImplementedError):
            await provider.create_online_evaluation_config("test", ["eval-1"])

        with pytest.raises(NotImplementedError):
            await provider.list_online_evaluation_configs()


@patch("agentic_primitives_gateway.primitives.evaluations.langfuse.get_service_credentials_or_defaults")
class TestHealthcheck:
    @pytest.mark.asyncio
    async def test_healthcheck_with_auth(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = LangfuseEvaluationsProvider(public_key="pk", secret_key="sk")

        with patch("langfuse.Langfuse") as mock_lf_cls:
            mock_lf = MagicMock()
            mock_lf.auth_check.return_value = True
            mock_lf_cls.return_value = mock_lf

            result = await provider.healthcheck()

            assert result is True

    @pytest.mark.asyncio
    async def test_healthcheck_reachable(self, mock_creds):
        mock_creds.return_value = {"public_key": "", "secret_key": "", "base_url": "https://test"}
        provider = LangfuseEvaluationsProvider()

        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_get.return_value = mock_resp

            result = await provider.healthcheck()

            assert result == "reachable"

    @pytest.mark.asyncio
    async def test_healthcheck_exception(self, mock_creds):
        mock_creds.side_effect = Exception("boom")
        provider = LangfuseEvaluationsProvider()

        result = await provider.healthcheck()

        assert result is False


@patch("agentic_primitives_gateway.primitives.evaluations.langfuse.get_service_credentials_or_defaults")
class TestCredentialResolution:
    @pytest.mark.asyncio
    async def test_resolve_rest_client(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = LangfuseEvaluationsProvider(public_key="pk", secret_key="sk")

        with patch("agentic_primitives_gateway.primitives.evaluations.langfuse.LangfuseAPI") as mock_cls:
            provider._resolve_rest_client()
            mock_cls.assert_called_once_with(
                base_url="https://test",
                username="pk",
                password="sk",
            )

    @pytest.mark.asyncio
    async def test_resolve_sdk_client(self, mock_creds):
        mock_creds.return_value = {"public_key": "pk", "secret_key": "sk", "base_url": "https://test"}
        provider = LangfuseEvaluationsProvider(public_key="pk", secret_key="sk")

        with patch("langfuse.Langfuse") as mock_cls:
            provider._resolve_sdk_client()
            mock_cls.assert_called_once_with(
                public_key="pk",
                secret_key="sk",
                base_url="https://test",
            )


class TestHelpers:
    def test_to_data_type(self):
        from langfuse.api import ScoreDataType

        assert _to_data_type("numeric") == ScoreDataType.NUMERIC
        assert _to_data_type("boolean") == ScoreDataType.BOOLEAN
        assert _to_data_type("categorical") == ScoreDataType.CATEGORICAL
        assert _to_data_type("unknown") == ScoreDataType.NUMERIC

    def test_score_config_to_dict(self):
        config = _mock_score_config(is_archived=True)
        result = _score_config_to_dict(config)
        assert result["evaluator_id"] == "cfg-1"
        assert result["status"] == "ARCHIVED"
        assert result["config"]["min_value"] == 0.0

    def test_score_to_dict(self):
        score = _mock_score(comment="great")
        result = _score_to_dict(score)
        assert result["score_id"] == "score-1"
        assert result["value"] == 0.9
        assert result["comment"] == "great"
