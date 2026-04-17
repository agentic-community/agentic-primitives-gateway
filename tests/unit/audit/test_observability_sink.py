from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.audit.models import AuditAction, AuditEvent, AuditOutcome
from agentic_primitives_gateway.audit.sinks.observability import ObservabilityProviderSink


@pytest.mark.asyncio
async def test_delegates_to_registry_observability_ingest_log():
    sink = ObservabilityProviderSink()
    event = AuditEvent(
        action=AuditAction.AUTH_SUCCESS,
        outcome=AuditOutcome.SUCCESS,
        actor_id="alice",
        metadata={"backend": "jwt"},
    )

    fake_registry = AsyncMock()
    fake_registry.observability.ingest_log = AsyncMock()
    with patch(
        "agentic_primitives_gateway.registry.registry",
        fake_registry,
    ):
        await sink.emit(event)

    fake_registry.observability.ingest_log.assert_awaited_once()
    payload = fake_registry.observability.ingest_log.call_args.args[0]
    assert payload["action"] == "auth.success"
    assert payload["actor_id"] == "alice"
    assert payload["metadata"] == {"backend": "jwt"}


def test_sink_name_defaults_and_overrides():
    assert ObservabilityProviderSink().name == "observability"
    assert ObservabilityProviderSink(name="custom").name == "custom"
