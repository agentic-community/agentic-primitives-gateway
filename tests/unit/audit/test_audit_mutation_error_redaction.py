"""Intent-level test: audit_mutation emits only safe error data on exception.

Contract (CLAUDE.md audit_mutation): on exception, emit one failure
event with ``metadata.error_type`` (the exception class name, not
its str()).  Users need to be able to debug which mutation failed
without the audit trail leaking credentials that may live inside
the exception's message.

Existing tests (``test_audit_mutation.py``) cover the happy-path and
basic failure emission.  They don't verify:

- The emitted ``error_type`` is the class name only, not a str()
  of the exception (which could contain a token).
- The exception's message itself is NOT copied verbatim into
  metadata anywhere.
- Metadata set on ``audit.metadata`` by the handler before the
  exception still gets emitted (so handlers can record context
  for debugging without losing it on failure).

This file covers those.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from agentic_primitives_gateway.audit.emit import audit_mutation


@pytest.fixture
def captured_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def _emit_stub(**kwargs):
        events.append(kwargs)

    with patch("agentic_primitives_gateway.audit.emit.emit_audit_event", side_effect=_emit_stub):
        yield events


class TestErrorTypeOnly:
    @pytest.mark.asyncio
    async def test_exception_message_not_leaked_in_metadata(self, captured_events):
        """An exception whose str() contains a Bearer token → the
        audit event's metadata.error_type is the class name only,
        never the str().
        """
        token = "Bearer sekret-xyz-123"

        class MyProviderError(Exception):
            pass

        with pytest.raises(MyProviderError):
            async with audit_mutation("test.action"):
                raise MyProviderError(f"HTTP 401 with Authorization: {token}")

        assert len(captured_events) == 1
        ev = captured_events[0]
        meta = ev.get("metadata", {})

        # error_type is the class name, not the str().
        assert meta.get("error_type") == "MyProviderError"
        # Crucially: no field in metadata contains the secret.
        for key, value in meta.items():
            assert token not in str(value), f"Secret leaked into audit metadata.{key}: {value!r}"
            assert "sekret" not in str(value), f"Secret leaked: {key}={value!r}"


class TestCustomMetadataPreservedOnFailure:
    @pytest.mark.asyncio
    async def test_metadata_set_by_handler_before_exception_is_emitted(self, captured_events):
        """If the handler sets ``audit.metadata`` fields before the
        exception is raised, those fields must appear in the failure
        event — handlers use this to record useful debugging context.
        """

        class MyError(Exception):
            pass

        with pytest.raises(MyError):
            async with audit_mutation("test.action") as audit:
                audit.metadata["name"] = "my-resource"
                audit.metadata["version"] = 3
                raise MyError("bang")

        assert len(captured_events) == 1
        ev = captured_events[0]
        meta = ev.get("metadata", {})
        # Handler-set fields still present.
        assert meta.get("name") == "my-resource"
        assert meta.get("version") == 3
        # error_type also added.
        assert meta.get("error_type") == "MyError"

    @pytest.mark.asyncio
    async def test_handler_metadata_with_secret_value_NOT_auto_redacted(self, captured_events):
        """A handler that puts a raw credential in its metadata is
        its own problem — audit_mutation does NOT auto-redact
        unknown fields (we don't pattern-scan metadata values).
        This test documents that contract so future readers know
        the boundary.

        If this behavior changes (auto-scrub metadata values), the
        test assertion should be updated — flagging it as an
        intentional contract decision, not a bug.
        """

        class MyError(Exception):
            pass

        with pytest.raises(MyError):
            async with audit_mutation("test.action") as audit:
                # Handler deliberately put raw token in metadata.
                audit.metadata["api_response"] = "auth: Bearer raw-token-xyz"
                raise MyError("timeout")

        ev = captured_events[0]
        meta = ev.get("metadata", {})
        # Today: the raw value is in the event.  Handlers must be
        # responsible for what they put into metadata.
        assert "raw-token-xyz" in meta.get("api_response", ""), (
            "audit_mutation now auto-redacts metadata values — update the "
            "contract documentation in CLAUDE.md and this test."
        )


class TestSuccessPathSanity:
    @pytest.mark.asyncio
    async def test_success_emits_no_error_type(self, captured_events):
        """On clean exit, the success event must not contain
        error_type.  Guards against a regression where the error
        metadata leaked into the success event.
        """
        async with audit_mutation("test.action") as audit:
            audit.metadata["name"] = "my-resource"

        assert len(captured_events) == 1
        ev = captured_events[0]
        meta = ev.get("metadata", {})
        assert "error_type" not in meta, f"Success event carried error_type: {meta}"
        assert meta.get("name") == "my-resource"


class TestDurationAlwaysEmitted:
    @pytest.mark.asyncio
    async def test_failure_event_carries_duration(self, captured_events):
        """Both success and failure events carry ``duration_ms`` so
        operators can see how long a failing mutation took.
        """

        class MyError(Exception):
            pass

        with pytest.raises(MyError):
            async with audit_mutation("test.action"):
                raise MyError("x")

        assert len(captured_events) == 1
        ev = captured_events[0]
        assert "duration_ms" in ev
        assert ev["duration_ms"] >= 0
