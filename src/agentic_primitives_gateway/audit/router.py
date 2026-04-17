"""Fan-out router for audit events.

Holds one :class:`AuditSink` per configured destination and delivers each
:class:`AuditEvent` to every sink through an isolated per-sink async
worker.  Design properties:

* **Non-blocking emit** — ``emit()`` is synchronous and returns immediately
  after ``put_nowait`` on each sink queue.  Request-path latency cost is
  one Pydantic construct plus N enqueues.
* **Failure isolation** — each sink has its own queue and worker, so a
  slow or broken sink cannot stall others (or the request path).
* **Backpressure** — queue full drops the event for that sink and
  increments ``gateway_audit_events_dropped_total``.
* **Bounded sink latency** — every ``sink.emit`` call runs under
  ``asyncio.wait_for`` with a configurable timeout.
* **Graceful shutdown** — ``shutdown()`` drains each queue (with a total
  deadline), cancels workers, and calls ``sink.close()`` on each sink.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.models import AuditEvent

logger = logging.getLogger(__name__)


class AuditRouter:
    """Fan-out dispatcher for :class:`AuditEvent` across multiple sinks."""

    def __init__(
        self,
        sinks: list[AuditSink],
        queue_size: int = 2048,
        sink_timeout_seconds: float = 2.0,
    ) -> None:
        if not sinks:
            raise ValueError("AuditRouter requires at least one sink")
        # Disallow duplicate names — the queue key is the sink name.
        names = [s.name for s in sinks]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate audit sink names: {names}")

        self._sinks: list[AuditSink] = sinks
        self._queue_size = queue_size
        self._sink_timeout = sink_timeout_seconds
        self._queues: dict[str, asyncio.Queue[AuditEvent | None]] = {}
        self._workers: list[asyncio.Task[None]] = []
        self._started = False

    @property
    def sinks(self) -> list[AuditSink]:
        return list(self._sinks)

    async def start(self) -> None:
        """Start per-sink worker tasks.  Idempotent."""
        if self._started:
            return
        for sink in self._sinks:
            queue: asyncio.Queue[AuditEvent | None] = asyncio.Queue(maxsize=self._queue_size)
            self._queues[sink.name] = queue
            self._workers.append(asyncio.create_task(self._drain(sink, queue), name=f"audit-{sink.name}"))
        self._started = True

    def emit(self, event: AuditEvent) -> None:
        """Enqueue ``event`` on every sink queue.  Non-blocking."""
        if not self._started:
            # Router not yet started or already torn down — drop.
            for sink in self._sinks:
                metrics.AUDIT_EVENTS_DROPPED.labels(sink=sink.name, reason="not_started").inc()
            return

        for sink in self._sinks:
            queue = self._queues[sink.name]
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                metrics.AUDIT_EVENTS_DROPPED.labels(sink=sink.name, reason="queue_full").inc()
                continue
            metrics.AUDIT_SINK_QUEUE_DEPTH.labels(sink=sink.name).set(queue.qsize())

    async def _drain(self, sink: AuditSink, queue: asyncio.Queue[AuditEvent | None]) -> None:
        while True:
            item = await queue.get()
            metrics.AUDIT_SINK_QUEUE_DEPTH.labels(sink=sink.name).set(queue.qsize())
            if item is None:
                # Sentinel — flush then exit.
                try:
                    await sink.flush()
                finally:
                    queue.task_done()
                return
            try:
                await asyncio.wait_for(sink.emit(item), timeout=self._sink_timeout)
                metrics.AUDIT_SINK_EVENTS.labels(sink=sink.name, outcome="success").inc()
            except TimeoutError:
                metrics.AUDIT_SINK_EVENTS.labels(sink=sink.name, outcome="timeout").inc()
                logger.warning("Audit sink %s timed out after %.1fs", sink.name, self._sink_timeout)
            except Exception:
                metrics.AUDIT_SINK_EVENTS.labels(sink=sink.name, outcome="error").inc()
                logger.exception("Audit sink %s raised while emitting event", sink.name)
            finally:
                queue.task_done()

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Drain queues, stop workers, close sinks.  Best-effort.

        Each sink gets a sentinel ``None`` so its worker flushes and
        exits cleanly.  The whole shutdown is bounded by ``timeout``;
        any worker still running after the deadline is cancelled.
        """
        if not self._started:
            return

        for queue in self._queues.values():
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

        try:
            await asyncio.wait_for(
                asyncio.gather(*self._workers, return_exceptions=True),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("Audit shutdown timed out after %.1fs — cancelling workers", timeout)
            for task in self._workers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._workers, return_exceptions=True)

        for sink in self._sinks:
            try:
                await sink.close()
            except Exception:
                logger.exception("Audit sink %s raised during close()", sink.name)

        self._workers.clear()
        self._queues.clear()
        self._started = False
