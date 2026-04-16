"""Integration tests for the Langfuse evaluations primitive.

Full stack with real Langfuse calls:
AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
LangfuseEvaluationsProvider -> real Langfuse API.

Requires:
  - A running Langfuse instance
  - LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY env vars
  - Optionally LANGFUSE_BASE_URL (default: http://localhost:3000)
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration


# ── Skip logic ────────────────────────────────────────────────────────

if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
    pytest.skip(
        "LANGFUSE_PUBLIC_KEY not set — skipping Langfuse evaluations integration tests",
        allow_module_level=True,
    )

FAKE_AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
FAKE_AWS_REGION = "us-east-1"


# ── Registry initialization ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with Langfuse evaluations provider."""
    public_key = os.environ["LANGFUSE_PUBLIC_KEY"]
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "http://localhost:3000")

    test_settings = Settings(
        allow_server_credentials="always",
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.noop.NoopMemoryProvider",
                "config": {},
            },
            "observability": {
                "backend": (
                    "agentic_primitives_gateway.primitives.observability.langfuse.LangfuseObservabilityProvider"
                ),
                "config": {
                    "public_key": public_key,
                    "secret_key": secret_key,
                    "base_url": base_url,
                },
            },
            "evaluations": {
                "backend": ("agentic_primitives_gateway.primitives.evaluations.langfuse.LangfuseEvaluationsProvider"),
                "config": {
                    "public_key": public_key,
                    "secret_key": secret_key,
                    "base_url": base_url,
                },
            },
            "llm": {
                "backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider",
                "config": {},
            },
            "tools": {
                "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
                "config": {},
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


# ── Client fixture ───────────────────────────────────────────────────


@pytest.fixture
async def client():
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


# ── Evaluator CRUD (Score Configs) ───────────────────────────────────


class TestEvaluatorLifecycle:
    @pytest.mark.asyncio
    async def test_create_and_get_evaluator(self, client):
        result = await client.create_evaluator(
            name="integ-helpfulness",
            evaluator_type="numeric",
            config={"min_value": 0.0, "max_value": 1.0},
            description="Integration test evaluator",
        )

        assert "evaluator_id" in result
        assert result["name"] == "integ-helpfulness"
        assert result["status"] == "ACTIVE"

        # Get it back
        info = await client.get_evaluator(result["evaluator_id"])
        assert info["evaluator_id"] == result["evaluator_id"]
        assert info["name"] == "integ-helpfulness"

    @pytest.mark.asyncio
    async def test_list_evaluators(self, client):
        await client.create_evaluator(
            name="integ-list-test",
            evaluator_type="numeric",
        )

        result = await client.list_evaluators()

        assert "evaluators" in result
        assert len(result["evaluators"]) > 0

    @pytest.mark.asyncio
    async def test_update_evaluator(self, client):
        created = await client.create_evaluator(
            name="integ-update-test",
            evaluator_type="boolean",
            description="original",
        )
        evaluator_id = created["evaluator_id"]

        updated = await client.update_evaluator(evaluator_id, description="updated description")

        assert updated["description"] == "updated description"

    @pytest.mark.asyncio
    async def test_delete_evaluator_archives(self, client):
        """Delete archives the score config in Langfuse (no hard delete)."""
        created = await client.create_evaluator(
            name="integ-delete-test",
            evaluator_type="numeric",
        )
        evaluator_id = created["evaluator_id"]

        await client.delete_evaluator(evaluator_id)

        info = await client.get_evaluator(evaluator_id)
        assert info["status"] == "ARCHIVED"


# ── Score CRUD ───────────────────────────────────────────────────────


class TestScoreCRUD:
    @pytest.fixture
    async def trace_id(self, client):
        """Ingest a trace and return its ID for scoring."""
        from uuid import uuid4

        tid = uuid4().hex
        await client.ingest_trace({"name": "integ-eval-trace", "trace_id": tid})
        await client.flush_observability()
        await asyncio.sleep(1)  # Langfuse eventual consistency
        return tid

    @pytest.mark.asyncio
    async def test_create_score(self, client, trace_id):
        result = await client.create_evaluation_score(
            name="accuracy",
            value=0.92,
            trace_id=trace_id,
            comment="Good response",
            data_type="NUMERIC",
        )

        assert "score_id" in result
        assert result["name"] == "accuracy"
        assert result["value"] == 0.92

    @pytest.mark.asyncio
    async def test_create_and_get_score(self, client, trace_id):
        created = await client.create_evaluation_score(
            name="relevance",
            value=0.85,
            trace_id=trace_id,
        )

        # Retry with delays for Langfuse eventual consistency
        score = None
        for _ in range(5):
            await asyncio.sleep(2)
            try:
                score = await client.get_evaluation_score(created["score_id"])
                break
            except Exception:
                continue

        assert score is not None, f"Could not retrieve score {created['score_id']} after retries"
        assert score["score_id"] == created["score_id"]
        assert score["name"] == "relevance"

    @pytest.mark.asyncio
    async def test_list_scores(self, client, trace_id):
        await client.create_evaluation_score(
            name="helpfulness",
            value=0.9,
            trace_id=trace_id,
        )

        # Retry for Langfuse eventual consistency
        result = None
        for _ in range(5):
            await asyncio.sleep(2)
            result = await client.list_evaluation_scores(trace_id=trace_id)
            if result.get("scores"):
                break

        assert result is not None
        assert "scores" in result
        assert len(result["scores"]) > 0

    @pytest.mark.asyncio
    async def test_delete_score(self, client, trace_id):
        created = await client.create_evaluation_score(
            name="to-delete",
            value=0.5,
            trace_id=trace_id,
        )
        await asyncio.sleep(2)

        await client.delete_evaluation_score(created["score_id"])
        await asyncio.sleep(2)

        # Verify it's gone — should raise 404
        from agentic_primitives_gateway_client.client import AgenticPlatformError

        with pytest.raises(AgenticPlatformError):
            await client.get_evaluation_score(created["score_id"])

    @pytest.mark.asyncio
    async def test_categorical_score(self, client, trace_id):
        result = await client.create_evaluation_score(
            name="sentiment",
            value="positive",
            trace_id=trace_id,
            data_type="CATEGORICAL",
        )

        assert result["value"] == "positive"

    @pytest.mark.asyncio
    async def test_list_scores_with_name_filter(self, client, trace_id):
        await client.create_evaluation_score(name="filter-test", value=1.0, trace_id=trace_id)
        await asyncio.sleep(1)

        result = await client.list_evaluation_scores(name="filter-test")

        assert "scores" in result
        for s in result["scores"]:
            assert s["name"] == "filter-test"


# ── Evaluate (record via evaluate endpoint) ──────────────────────────


class TestEvaluate:
    @pytest.fixture
    async def trace_id(self, client):
        from uuid import uuid4

        tid = uuid4().hex
        await client.ingest_trace({"name": "integ-evaluate-trace", "trace_id": tid})
        await client.flush_observability()
        await asyncio.sleep(1)
        return tid

    @pytest.mark.asyncio
    async def test_evaluate_with_value(self, client, trace_id):
        result = await client.evaluate(
            evaluator_id="helpfulness",
            target=trace_id,
            output_data="The capital of France is Paris.",
            metadata={"value": 0.95},
        )

        assert result["evaluator_id"] == "helpfulness"
        assert result["results"][0]["value"] == 0.95
        assert result["results"][0]["trace_id"] == trace_id

    @pytest.mark.asyncio
    async def test_evaluate_default_value(self, client, trace_id):
        result = await client.evaluate(
            evaluator_id="quality",
            target=trace_id,
            output_data="Good answer",
        )

        assert result["results"][0]["value"] == 1.0
