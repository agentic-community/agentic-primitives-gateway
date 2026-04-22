"""Invariant: every audit emit call site sets ``resource_type``.

A ``resource_type``-less event can't be filtered by type in the audit UI
and shows up as a ``null``-typed row in SIEMs.  The gateway's contract
is that every audited action names the kind of resource it touched —
this test enforces that contract at import time by scanning the source
tree.

If a new ``emit_audit_event(...)`` or ``audit_mutation(...)`` site is
added without ``resource_type=``, this test fails with the file+line
so the author picks an appropriate ``ResourceType`` or adds a mapping
in ``audit/models.py::PRIMITIVE_RESOURCE_TYPE``.

A second test below exercises the runtime path: every ``ResourceType``
is emitted through the real ``AuditRouter`` into a collector sink, and
the resulting event is asserted complete (action, outcome, resource
type/id, timestamp) and JSON round-trippable — the shape a sink sees.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

import agentic_primitives_gateway
from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import emit_audit_event, set_audit_router
from agentic_primitives_gateway.audit.models import AuditEvent, AuditOutcome, ResourceType
from agentic_primitives_gateway.audit.router import AuditRouter

_EMIT_CALL_RE = re.compile(r"\b(emit_audit_event|audit_mutation)\s*\(", re.M)


def _iter_emit_calls(text: str):
    """Yield (fn_name, start, full_call_text) for every balanced emit call.

    Uses a simple paren-depth scan — handles nested parens inside args
    (e.g. ``metadata={"k": some_fn(x)}``) where a regex cannot.
    """
    for match in _EMIT_CALL_RE.finditer(text):
        fn_name = match.group(1)
        i = text.index("(", match.start())
        depth = 1
        j = i + 1
        while depth > 0 and j < len(text):
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        yield fn_name, match.start(), text[match.start() : j]


def test_every_emit_site_sets_resource_type() -> None:
    """Scan src/ for emit calls missing ``resource_type=`` or setting it to ``None``."""
    pkg_root = Path(agentic_primitives_gateway.__file__).parent
    # Reject explicit ``resource_type=None`` — the keyword is present but
    # the value is useless.  Tolerant to whitespace (``resource_type =
    # None``, ``resource_type=None,``).
    none_re = re.compile(r"\bresource_type\s*=\s*None\b")
    offenders: list[str] = []
    for py_file in pkg_root.rglob("*.py"):
        text = py_file.read_text()
        for fn_name, start, call_text in _iter_emit_calls(text):
            if "resource_type" in call_text and not none_re.search(call_text):
                continue
            line_no = text[:start].count("\n") + 1
            rel = py_file.relative_to(pkg_root.parent.parent)
            offenders.append(f"{rel}:{line_no} — {fn_name}(...)")

    assert not offenders, (
        "The following audit emit sites do not set resource_type. Every "
        "audited action must name the kind of resource it operated on.\n"
        "If the primitive name is the right label, use "
        "PRIMITIVE_RESOURCE_TYPE from audit/models.py. Otherwise, pick an "
        "appropriate ResourceType enum member.\n\n  " + "\n  ".join(offenders)
    )


class _CollectorSink(AuditSink):
    def __init__(self) -> None:
        self.name = "collector"
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


@pytest.fixture
async def collector_router():
    sink = _CollectorSink()
    router = AuditRouter([sink])
    await router.start()
    set_audit_router(router)
    try:
        yield sink
    finally:
        # Give the per-sink queue a tick to drain before shutdown.
        await asyncio.sleep(0.02)
        await router.shutdown(timeout=1.0)
        set_audit_router(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("resource_type", list(ResourceType))
async def test_every_resource_type_emits_a_complete_event(
    collector_router: _CollectorSink, resource_type: ResourceType
) -> None:
    """Every ResourceType member flows end-to-end and produces a usable event.

    This catches: (1) a new enum member that no emit helper supports,
    (2) a router shutdown order bug that drops events mid-flight,
    (3) a ``model_dump_json`` regression that would break sinks.
    """
    emit_audit_event(
        action=f"test.{resource_type.value}",
        outcome=AuditOutcome.SUCCESS,
        resource_type=resource_type,
        resource_id=f"{resource_type.value}-42",
        metadata={"probe": True},
    )
    # Emit is synchronous, delivery is via an asyncio.Queue.
    await asyncio.sleep(0.02)

    matches = [e for e in collector_router.events if e.action == f"test.{resource_type.value}"]
    assert len(matches) == 1, f"Expected exactly one event for {resource_type.value}, got {len(matches)}"
    event = matches[0]

    # Top-level fields the UI + sinks depend on.
    assert event.resource_type == resource_type
    assert event.resource_id == f"{resource_type.value}-42"
    assert event.outcome == AuditOutcome.SUCCESS
    assert event.schema_version == "1"
    assert event.event_id  # non-empty default
    assert event.timestamp is not None
    assert event.metadata == {"probe": True}

    # Wire-format round-trip: what every sink writes out.
    wire = json.loads(event.model_dump_json())
    assert wire["resource_type"] == resource_type.value
    assert wire["action"] == f"test.{resource_type.value}"
    assert wire["outcome"] == "success"


def test_primitive_resource_type_covers_every_primitive() -> None:
    """Adding a new ``Primitive`` must come with a ``PRIMITIVE_RESOURCE_TYPE`` entry.

    Without this test, a new primitive would silently emit
    ``provider.call`` / ``provider.healthcheck`` events with
    ``resource_type=None`` — the exact gap that motivated this test file.
    """
    from agentic_primitives_gateway.audit.models import PRIMITIVE_RESOURCE_TYPE
    from agentic_primitives_gateway.models.enums import Primitive

    mapped = set(PRIMITIVE_RESOURCE_TYPE.keys())
    canonical = {p.value for p in Primitive}
    missing = canonical - mapped
    extra = mapped - canonical
    assert not missing, (
        f"PRIMITIVE_RESOURCE_TYPE is missing entries for: {sorted(missing)}. "
        f"Add them in audit/models.py so cross-cutting emitters set resource_type."
    )
    assert not extra, f"PRIMITIVE_RESOURCE_TYPE has stale entries for non-primitives: {sorted(extra)}."


def test_ts_resource_type_list_matches_python_enum() -> None:
    """The TS ``AUDIT_RESOURCE_TYPES`` array drives the UI filter dropdown;
    it must stay in sync with the server ``ResourceType`` enum.

    Parses the array out of ``ui/src/api/types.ts`` with a regex rather
    than running the TS compiler — cheap, and good enough to catch the
    "added a value server-side but forgot to update the UI" drift.
    """
    pkg_root = Path(agentic_primitives_gateway.__file__).parent
    ts_path = pkg_root.parent.parent / "ui" / "src" / "api" / "types.ts"
    assert ts_path.exists(), f"UI types file missing: {ts_path}"

    text = ts_path.read_text()
    match = re.search(
        r"export const AUDIT_RESOURCE_TYPES\s*=\s*\[(.*?)\]\s*as const",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, "Could not parse AUDIT_RESOURCE_TYPES from types.ts"
    ts_values = set(re.findall(r'"([^"]+)"', match.group(1)))

    server_values = {rt.value for rt in ResourceType}
    missing_from_ui = server_values - ts_values
    extra_in_ui = ts_values - server_values
    assert not missing_from_ui, (
        f"UI AUDIT_RESOURCE_TYPES is missing values present in ResourceType: "
        f"{sorted(missing_from_ui)}. Update ui/src/api/types.ts."
    )
    assert not extra_in_ui, (
        f"UI AUDIT_RESOURCE_TYPES has values not in ResourceType: "
        f"{sorted(extra_in_ui)}. Remove stale entries from ui/src/api/types.ts."
    )


def test_ts_outcome_list_matches_python_enum() -> None:
    """Same drift check for ``AUDIT_OUTCOMES`` / ``AuditOutcome``."""
    pkg_root = Path(agentic_primitives_gateway.__file__).parent
    ts_path = pkg_root.parent.parent / "ui" / "src" / "api" / "types.ts"
    text = ts_path.read_text()
    match = re.search(
        r"export const AUDIT_OUTCOMES\s*=\s*\[(.*?)\]\s*as const",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, "Could not parse AUDIT_OUTCOMES from types.ts"
    ts_values = set(re.findall(r'"([^"]+)"', match.group(1)))
    server_values = {o.value for o in AuditOutcome}
    assert ts_values == server_values, (
        f"UI AUDIT_OUTCOMES drift: missing {sorted(server_values - ts_values)}, "
        f"extra {sorted(ts_values - server_values)}"
    )
