"""LLM-specific audit + metrics wrappers used by ``LLMProvider.__init_subclass__``.

Every LLM provider subclass gets ``route_request`` and
``route_request_stream`` wrapped automatically to emit an
``llm.generate`` audit event with model + input/output/total token
counts, and to increment ``gateway_llm_requests_total`` and
``gateway_llm_tokens_total`` with bounded labels (model, kind).

This lives in a separate module so the ABC itself stays dependency-light:
the audit subsystem is imported lazily when subclasses are defined, not
when ``LLMProvider`` itself is imported (it's imported extremely early
during app bootstrap).
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator
from typing import Any

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType


def _resolve_model(model_request: dict[str, Any], response: dict[str, Any] | None) -> str:
    """Prefer the model the provider reports over the one the caller asked for."""
    if response and response.get("model"):
        return str(response["model"])
    return str(model_request.get("model") or "")


def _emit_llm_event(
    model: str,
    outcome: AuditOutcome,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    error_type: str | None = None,
) -> None:
    status = "success" if outcome == AuditOutcome.SUCCESS else "failure"
    metrics.LLM_REQUESTS.labels(model=model or "unknown", status=status).inc()
    if input_tokens:
        metrics.LLM_TOKENS.labels(model=model or "unknown", kind="input").inc(input_tokens)
    if output_tokens:
        metrics.LLM_TOKENS.labels(model=model or "unknown", kind="output").inc(output_tokens)
    total = (input_tokens or 0) + (output_tokens or 0)
    if total:
        metrics.LLM_TOKENS.labels(model=model or "unknown", kind="total").inc(total)

    metadata: dict[str, Any] = {"model": model}
    if input_tokens is not None:
        metadata["input_tokens"] = input_tokens
    if output_tokens is not None:
        metadata["output_tokens"] = output_tokens
    if error_type:
        metadata["error_type"] = error_type
    emit_audit_event(
        action=AuditAction.LLM_GENERATE,
        outcome=outcome,
        resource_type=ResourceType.LLM,
        resource_id=model or None,
        metadata=metadata,
    )


def wrap_route_request(func: Any) -> Any:
    """Wrap a coroutine ``route_request`` implementation with audit + metrics."""

    @functools.wraps(func)
    async def wrapper(self: Any, model_request: dict[str, Any]) -> dict[str, Any]:
        try:
            response: dict[str, Any] = await func(self, model_request)
        except Exception as exc:
            _emit_llm_event(
                _resolve_model(model_request, None),
                AuditOutcome.ERROR,
                error_type=type(exc).__name__,
            )
            raise
        usage = response.get("usage") or {}
        _emit_llm_event(
            _resolve_model(model_request, response),
            AuditOutcome.SUCCESS,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )
        return response

    return wrapper


def wrap_route_request_stream(func: Any) -> Any:
    """Wrap an async-generator ``route_request_stream`` with audit + metrics.

    Token counts are pulled from ``{"type": "metadata"}`` and
    ``{"type": "message_stop"}`` events as they stream by — both Bedrock
    and the generic-provider fallback emit one of these at the tail.
    """

    @functools.wraps(func)
    async def wrapper(self: Any, model_request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        input_tokens = 0
        output_tokens = 0
        observed_model = ""
        error_type: str | None = None
        outcome = AuditOutcome.ERROR
        try:
            async for parsed in func(self, model_request):
                if isinstance(parsed, dict):
                    if parsed.get("type") == "metadata":
                        usage = parsed.get("usage") or {}
                        input_tokens = usage.get("input_tokens", 0) or input_tokens
                        output_tokens = usage.get("output_tokens", 0) or output_tokens
                    elif parsed.get("type") == "message_stop":
                        usage = parsed.get("usage") or {}
                        input_tokens = usage.get("input_tokens", input_tokens) or input_tokens
                        output_tokens = usage.get("output_tokens", output_tokens) or output_tokens
                        if parsed.get("model"):
                            observed_model = parsed["model"]
                yield parsed
            outcome = AuditOutcome.SUCCESS
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            model = observed_model or _resolve_model(model_request, None)
            _emit_llm_event(
                model,
                outcome,
                input_tokens=input_tokens or None,
                output_tokens=output_tokens or None,
                error_type=error_type,
            )

    return wrapper
