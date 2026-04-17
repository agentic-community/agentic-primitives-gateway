"""Stdout JSON sink — always-on audit stream for k8s/Fluent Bit/Loki/Datadog.

Writes one JSON line per event to ``sys.stdout``.  Pydantic's
``model_dump_json()`` handles timezone-aware datetimes and enums natively,
so the output is a valid JSON object with no ad-hoc encoding.

Writes use ``print`` with ``flush=True`` so events are visible in
real time in ``kubectl logs`` and similar tools.  For very high throughput
this can be adjusted to buffered writes, but today the bottleneck is far
downstream and the simplest story wins.
"""

from __future__ import annotations

import sys
from typing import IO, Any

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent


class StdoutJsonSink(AuditSink):
    """JSON-line writer to stdout (or an injected stream for testing)."""

    def __init__(self, *, name: str = "stdout_json", stream: IO[str] | None = None, **_: Any) -> None:
        self.name = name
        self._stream = stream if stream is not None else sys.stdout

    async def emit(self, event: AuditEvent) -> None:
        line = event.model_dump_json()
        self._stream.write(line)
        self._stream.write("\n")
        self._stream.flush()
