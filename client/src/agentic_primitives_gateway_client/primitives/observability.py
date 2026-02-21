"""Observability helper for the Agentic Primitives Gateway client.

Provides simple trace and log methods backed by the platform's
observability endpoint.

Usage (async)::

    from agentic_primitives_gateway_client import AgenticPlatformClient, Observability

    client = AgenticPlatformClient("http://localhost:8000", ...)
    obs = Observability(client, namespace="agent:my-agent")

    await obs.trace("tool:remember", {"key": "k1"}, "stored")
    await obs.log("info", "Agent started")
    result = await obs.query_traces(limit=10)

Usage (sync)::

    obs.trace_sync("tool:remember", {"key": "k1"}, "stored")
    obs.log_sync("info", "Agent started")
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from agentic_primitives_gateway_client.client import AgenticPlatformClient

logger = logging.getLogger(__name__)


class Observability:
    """Observability helper backed by the Agentic Primitives Gateway."""

    def __init__(
        self,
        client: AgenticPlatformClient,
        namespace: str,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self._client = client
        self.namespace = namespace
        self.session_id = session_id or str(uuid.uuid4())[:8]
        self.tags = tags or []
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Async interface ─────────────────────────────────────────────

    async def trace(
        self,
        name: str,
        input_data: dict[str, Any],
        output: str,
        tags: list[str] | None = None,
    ) -> None:
        """Send a trace to the observability backend (best-effort)."""
        try:  # noqa: SIM105
            await self._client.ingest_trace(
                {
                    "trace_id": str(uuid.uuid4()),
                    "name": name,
                    "user_id": self.namespace,
                    "session_id": self.session_id,
                    "input": input_data,
                    "output": output,
                    "tags": tags or self.tags or ["tool-call"],
                    "spans": [{"name": name, "input": input_data, "output": output}],
                    "metadata": {"session": self.session_id},
                }
            )
        except Exception:
            pass

    async def log(self, level: str, message: str, **extra: str) -> None:
        """Send a log event to the observability backend (best-effort)."""
        try:  # noqa: SIM105
            await self._client.ingest_log(
                {
                    "level": level,
                    "message": message,
                    "metadata": {
                        "agent": self.namespace,
                        "session": self.session_id,
                        **extra,
                    },
                }
            )
        except Exception:
            pass

    async def query_traces(self, limit: int = 10) -> str:
        """Query recent traces from the observability backend."""
        try:
            result = await self._client.query_traces({"limit": limit})
            traces = result.get("traces", [])
            if not traces:
                return "No traces found."
            lines = [
                f"  [{t.get('trace_id', '?')[:8]}] {t.get('name', 'unnamed')} (tags: {t.get('tags', [])})"
                for t in traces
            ]
            return "Recent traces:\n" + "\n".join(lines)
        except Exception as e:
            return f"Failed to query traces: {e}"

    # ── Sync wrappers ───────────────────────────────────────────────

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _sync(self, coro: Any) -> Any:
        return self._get_loop().run_until_complete(coro)

    def trace_sync(
        self,
        name: str,
        input_data: dict[str, Any],
        output: str,
        tags: list[str] | None = None,
    ) -> None:
        self._sync(self.trace(name, input_data, output, tags))

    def log_sync(self, level: str, message: str, **extra: str) -> None:
        self._sync(self.log(level, message, **extra))

    def query_traces_sync(self, limit: int = 10) -> str:
        return str(self._sync(self.query_traces(limit)))
