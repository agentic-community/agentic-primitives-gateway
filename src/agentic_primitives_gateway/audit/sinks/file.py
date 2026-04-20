"""Rotating append-only file sink for audit events.

Writes one JSON line per event to a local file with size-based rotation
driven by :class:`logging.handlers.RotatingFileHandler`.  File output is
convenient for single-node dev and for compliance setups that pair this
with a log-shipping sidecar (Fluent Bit, Vector, Filebeat) that tails
the file rather than consuming stdout.

For multi-replica Kubernetes deployments the stdout sink is the
preferred default — a single file per pod is not useful as a shared
audit store.  Use ``redis_stream`` or an observability sink in that case.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
from pathlib import Path
from typing import Any

from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent


class RotatingFileAuditSink(AuditSink):
    """Append JSON-line events to a rotating file."""

    def __init__(
        self,
        *,
        name: str = "file",
        path: str,
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
        **_: Any,
    ) -> None:
        self.name = name
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

        # Reuse the stdlib rotating file handler for byte-based rotation +
        # atomic rename semantics — much less code than doing it ourselves.
        self._handler = logging.handlers.RotatingFileHandler(
            filename=str(self._path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )
        # We format the record ourselves (just the message) — the handler
        # must not decorate with timestamp/level since the event already
        # carries its own timestamp.
        self._handler.setFormatter(logging.Formatter("%(message)s"))

    async def emit(self, event: AuditEvent) -> None:
        line = event.model_dump_json()
        # The file write is synchronous I/O; offload to a thread so the
        # router worker doesn't block on disk.
        await asyncio.to_thread(self._write_line, line)

    def _write_line(self, line: str) -> None:
        record = logging.LogRecord(
            name="audit.file",
            level=logging.INFO,
            pathname=os.fspath(self._path),
            lineno=0,
            msg=line,
            args=(),
            exc_info=None,
        )
        self._handler.emit(record)

    async def close(self) -> None:
        await asyncio.to_thread(self._handler.close)
