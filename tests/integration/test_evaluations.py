"""Integration tests for the evaluations primitive.

Full stack with real AWS calls:
AgenticPlatformClient -> ASGI -> middleware -> route -> registry ->
AgentCoreEvaluationsProvider -> real AWS Bedrock AgentCore services.

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
async def evaluator(client):
    """Create an evaluator, yield ID, delete on teardown."""
    name = f"integ_eval_{uuid4().hex[:8]}"
    result = await client.create_evaluator(
        name=name,
        evaluator_type="TRACE",
        config={
            "evaluatorConfig": {
                "llmAsAJudge": {
                    "instructions": "Evaluate the helpfulness of {assistant_turn} given {context}.",
                    "ratingScale": {
                        "numerical": [
                            {"value": 1, "label": "Bad", "definition": "Bad response"},
                            {"value": 5, "label": "Good", "definition": "Good response"},
                        ],
                    },
                    "modelConfig": {
                        "bedrockEvaluatorModelConfig": {
                            "modelId": "us.anthropic.claude-sonnet-4-20250514-v1:0",
                        }
                    },
                }
            },
            "level": "TRACE",
        },
        description="integration test evaluator",
    )
    evaluator_id = result["evaluator_id"]
    # Wait for evaluator to become ACTIVE
    for _ in range(30):
        info = await client.get_evaluator(evaluator_id)
        if info.get("status", "").upper() == "ACTIVE":
            break
        await asyncio.sleep(1)
    yield evaluator_id
    with contextlib.suppress(Exception):
        await client.delete_evaluator(evaluator_id)


class TestEvaluatorLifecycle:
    @pytest.mark.asyncio
    async def test_create_and_get(self, client, evaluator):
        info = await client.get_evaluator(evaluator)
        assert info["evaluator_id"] == evaluator

    @pytest.mark.asyncio
    async def test_list_evaluators(self, client, evaluator):
        result = await client.list_evaluators()
        ids = [e["evaluator_id"] for e in result.get("evaluators", [])]
        assert evaluator in ids

    @pytest.mark.asyncio
    async def test_update_evaluator(self, client, evaluator):
        result = await client.update_evaluator(evaluator, description="updated by integration test")
        assert result.get("evaluator_id") == evaluator


class TestEvaluate:
    @pytest.mark.asyncio
    @pytest.mark.xfail(
        reason="AgentCore Evaluate API requires spans in internal trace format — needs deeper integration"
    )
    async def test_evaluate(self, client, evaluator):
        """Run an LLM-as-a-judge evaluation via AgentCore.

        AgentCore's Evaluate API requires evaluationInput with sessionSpans
        in the exact format produced by AgentCore's trace service. Constructing
        synthetic spans doesn't work — this needs real traced agent execution
        with span data fetched from the observability service.
        """
        result = await client.evaluate(
            evaluator_id=evaluator,
            metadata={
                "evaluationInput": {
                    "sessionSpans": [
                        {
                            "context": "What is the capital of France?",
                            "assistant_turn": "The capital of France is Paris.",
                        }
                    ]
                },
            },
        )
        assert "results" in result
