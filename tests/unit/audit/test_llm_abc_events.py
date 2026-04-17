"""llm.generate audit events + LLM token/request metrics come from LLMProvider ABC.

Every LLM provider gets wrapped automatically via ``__init_subclass__``,
so a dummy subclass is enough to verify the shape.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import set_audit_router
from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.primitives.llm.base import LLMProvider


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
async def audit_router():
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)
    try:
        yield sink
    finally:
        await router.shutdown(timeout=1.0)
        set_audit_router(None)


class _DummyLLM(LLMProvider):
    async def route_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        return {
            "model": model_request.get("model", "test-model"),
            "content": "hi",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 7},
        }

    async def list_models(self) -> list[dict[str, Any]]:
        return []


class _StreamingLLM(LLMProvider):
    async def route_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        return {"model": "x", "content": "", "usage": {}}

    async def list_models(self) -> list[dict[str, Any]]:
        return []

    async def route_request_stream(self, model_request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "content_delta", "delta": "he"}
        yield {"type": "content_delta", "delta": "llo"}
        yield {
            "type": "metadata",
            "usage": {"input_tokens": 3, "output_tokens": 5},
        }
        yield {
            "type": "message_stop",
            "stop_reason": "end_turn",
            "model": "stream-model",
            "usage": {"input_tokens": 3, "output_tokens": 5},
        }


class _FailingLLM(LLMProvider):
    async def route_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("down")

    async def list_models(self) -> list[dict[str, Any]]:
        return []


@pytest.mark.asyncio
async def test_route_request_emits_llm_generate_with_tokens(audit_router):
    llm = _DummyLLM()
    await llm.route_request({"model": "test-model"})
    await asyncio.sleep(0.02)

    gen_events = [e for e in audit_router.events if e.action == AuditAction.LLM_GENERATE]
    assert len(gen_events) == 1
    event = gen_events[0]
    assert event.outcome == AuditOutcome.SUCCESS
    assert event.resource_id == "test-model"
    assert event.metadata["model"] == "test-model"
    assert event.metadata["input_tokens"] == 10
    assert event.metadata["output_tokens"] == 7


@pytest.mark.asyncio
async def test_route_request_failure_emits_error_event(audit_router):
    llm = _FailingLLM()
    with pytest.raises(RuntimeError):
        await llm.route_request({"model": "broken-model"})
    await asyncio.sleep(0.02)

    gen_events = [e for e in audit_router.events if e.action == AuditAction.LLM_GENERATE]
    assert len(gen_events) == 1
    assert gen_events[0].outcome == AuditOutcome.ERROR
    assert gen_events[0].metadata["error_type"] == "RuntimeError"
    assert gen_events[0].resource_id == "broken-model"


@pytest.mark.asyncio
async def test_streaming_picks_up_tokens_from_metadata_event(audit_router):
    llm = _StreamingLLM()
    chunks = []
    async for ev in llm.route_request_stream({"model": "unused"}):
        chunks.append(ev)
    assert any(ev.get("type") == "content_delta" for ev in chunks)

    await asyncio.sleep(0.02)
    gen_events = [e for e in audit_router.events if e.action == AuditAction.LLM_GENERATE]
    assert len(gen_events) == 1
    event = gen_events[0]
    assert event.outcome == AuditOutcome.SUCCESS
    assert event.metadata["input_tokens"] == 3
    assert event.metadata["output_tokens"] == 5
    # Observed model from message_stop beats the (empty) requested model.
    assert event.metadata["model"] == "stream-model"


@pytest.mark.asyncio
async def test_llm_metrics_increment(audit_router):
    from agentic_primitives_gateway import metrics

    llm = _DummyLLM()

    req_before = metrics.LLM_REQUESTS.labels(model="test-model", status="success")._value.get()
    in_before = metrics.LLM_TOKENS.labels(model="test-model", kind="input")._value.get()
    out_before = metrics.LLM_TOKENS.labels(model="test-model", kind="output")._value.get()
    total_before = metrics.LLM_TOKENS.labels(model="test-model", kind="total")._value.get()

    await llm.route_request({"model": "test-model"})

    assert metrics.LLM_REQUESTS.labels(model="test-model", status="success")._value.get() == req_before + 1
    assert metrics.LLM_TOKENS.labels(model="test-model", kind="input")._value.get() == in_before + 10
    assert metrics.LLM_TOKENS.labels(model="test-model", kind="output")._value.get() == out_before + 7
    assert metrics.LLM_TOKENS.labels(model="test-model", kind="total")._value.get() == total_before + 17
