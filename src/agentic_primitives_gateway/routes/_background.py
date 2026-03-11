"""Shared utilities for background streaming runs.

Both agent chat and team run streaming endpoints decouple the run from the
HTTP connection by spawning a background ``asyncio.Task`` that feeds events
into a queue.  This module extracts the common dict tracking, cleanup, and
SSE generator logic.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from starlette.responses import StreamingResponse

# Type for entries: (task, queue, event_log, started_at)
# event_log is optional — agents don't use it, teams do.
RunEntry = tuple[asyncio.Task, asyncio.Queue, list[dict[str, Any]], float]


class BackgroundRunManager:
    """Tracks background asyncio tasks for streaming runs."""

    def __init__(self, stale_seconds: float = 600, grace_seconds: float = 0) -> None:
        self._runs: dict[str, RunEntry] = {}
        self._stale_seconds = stale_seconds
        self._grace_seconds = grace_seconds

    @property
    def runs(self) -> dict[str, RunEntry]:
        return self._runs

    def cleanup(self) -> None:
        """Remove completed or stale entries."""
        now = time.monotonic()
        to_remove = [
            key
            for key, (task, _, _, started) in self._runs.items()
            if (task.done() and (now - started > self._grace_seconds)) or (now - started > self._stale_seconds)
        ]
        for key in to_remove:
            self._runs.pop(key, None)

    def is_running(self, key: str) -> bool:
        entry = self._runs.get(key)
        return entry is not None and not entry[0].done()

    def get_status(self, key: str) -> str:
        if self.is_running(key):
            return "running"
        return "idle"

    def get_events(self, key: str) -> list[dict[str, Any]]:
        entry = self._runs.get(key)
        return entry[2] if entry else []

    def start(
        self,
        key: str,
        coro: Any,
        *,
        record_events: bool = False,
    ) -> tuple[asyncio.Queue, list[dict[str, Any]]]:
        """Spawn a background task that feeds events into a queue.

        Args:
            key: The run identifier (session_id or team_run_id).
            coro: An async generator that yields event dicts.
            record_events: If True, also accumulate events in a list for replay.

        Returns:
            (queue, event_log) — the SSE generator reads from the queue.
        """
        self.cleanup()

        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        event_log: list[dict[str, Any]] = []
        ctx = contextvars.copy_context()

        async def _run() -> None:
            try:
                async for event in coro:
                    if record_events:
                        event_log.append(event)
                    await queue.put(event)
            except Exception as exc:
                err = {"type": "error", "detail": str(exc)}
                if record_events:
                    event_log.append(err)
                await queue.put(err)
            finally:
                await queue.put(None)  # sentinel

        task = asyncio.create_task(_run(), context=ctx)
        self._runs[key] = (task, queue, event_log, time.monotonic())
        return queue, event_log

    def rekey(self, old_key: str, new_key: str) -> None:
        """Re-key a run entry (e.g. when team_run_id becomes known)."""
        entry = self._runs.pop(old_key, None)
        if entry is not None:
            self._runs[new_key] = entry


def sse_response(
    queue: asyncio.Queue[dict | None],
    *,
    strip_fields: frozenset[str] = frozenset(),
) -> StreamingResponse:
    """Create a StreamingResponse that reads from a background task queue."""

    async def _generate() -> AsyncIterator[str]:
        while True:
            event = await queue.get()
            if event is None:
                break
            if strip_fields:
                event = {k: v for k, v in event.items() if k not in strip_fields}
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
