from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agentic_primitives_gateway.audit.models import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
    ResourceType,
)


class TestAuditEvent:
    def test_defaults_fill_timestamp_and_event_id(self):
        before = datetime.now(tz=UTC)
        event = AuditEvent(action=AuditAction.AUTH_SUCCESS, outcome=AuditOutcome.SUCCESS)
        after = datetime.now(tz=UTC)

        assert event.schema_version == "1"
        assert event.event_id and len(event.event_id) == 32
        assert before <= event.timestamp <= after
        assert event.actor_id is None
        assert event.metadata == {}

    def test_is_frozen(self):
        event = AuditEvent(action="x", outcome=AuditOutcome.SUCCESS)
        with pytest.raises(ValidationError):
            event.action = "y"  # type: ignore[misc]

    def test_round_trips_through_json(self):
        event = AuditEvent(
            action=AuditAction.POLICY_DENY,
            outcome=AuditOutcome.DENY,
            resource_type=ResourceType.AGENT,
            resource_id="pirate",
            reason="not-owner",
            metadata={"tenant": "acme"},
        )
        raw = event.model_dump_json()
        parsed = json.loads(raw)
        assert parsed["action"] == "policy.deny"
        assert parsed["outcome"] == "deny"
        assert parsed["resource_type"] == "agent"
        assert parsed["resource_id"] == "pirate"
        assert parsed["metadata"] == {"tenant": "acme"}
        assert parsed["schema_version"] == "1"

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            AuditEvent(action="x", outcome=AuditOutcome.SUCCESS, unknown="nope")  # type: ignore[call-arg]


class TestAuditActionTaxonomy:
    def test_categories_use_dot_prefix(self):
        # Every action category used by the metrics label split on '.'
        actions = [
            AuditAction.AUTH_SUCCESS,
            AuditAction.AUTH_FAILURE,
            AuditAction.POLICY_ALLOW,
            AuditAction.POLICY_DENY,
            AuditAction.CREDENTIAL_WRITE,
            AuditAction.AGENT_RUN_START,
            AuditAction.TEAM_RUN_COMPLETE,
            AuditAction.TOOL_CALL,
            AuditAction.LLM_GENERATE,
            AuditAction.HTTP_REQUEST,
            AuditAction.RESOURCE_ACCESS_DENIED,
        ]
        for action in actions:
            assert "." in action, f"{action} must contain a '.' for category split"
