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

    # ── Extended async interface ─────────────────────────────────────

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        """Get a single trace by ID."""
        return await self._client.get_trace(trace_id)

    async def log_llm_call(
        self,
        trace_id: str,
        name: str,
        model: str,
        input_data: Any,
        output: Any,
        *,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Log an LLM generation call (best-effort)."""
        try:  # noqa: SIM105
            await self._client.log_generation(
                trace_id,
                {
                    "name": name,
                    "model": model,
                    "input": input_data,
                    "output": output,
                    "usage": usage,
                    "metadata": {"session": self.session_id},
                },
            )
        except Exception:
            pass

    async def score(
        self,
        trace_id: str,
        name: str,
        value: float,
        *,
        comment: str | None = None,
    ) -> None:
        """Attach a score to a trace (best-effort)."""
        try:
            score_body: dict[str, Any] = {"name": name, "value": value}
            if comment is not None:
                score_body["comment"] = comment
            await self._client.score_trace(trace_id, score_body)
        except Exception:
            pass

    async def get_sessions(self, limit: int = 10) -> str:
        """List observability sessions, returning formatted string."""
        try:
            result = await self._client.list_observability_sessions(limit=limit)
            sessions = result.get("sessions", [])
            if not sessions:
                return "No sessions found."
            lines = [f"  [{s.get('session_id', '?')[:8]}] traces={s.get('trace_count', 0)}" for s in sessions]
            return "Sessions:\n" + "\n".join(lines)
        except Exception as e:
            return f"Failed to list sessions: {e}"

    async def flush(self) -> None:
        """Force flush pending telemetry (best-effort)."""
        try:  # noqa: SIM105
            await self._client.flush_observability()
        except Exception:
            pass

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

    def get_trace_sync(self, trace_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._sync(self.get_trace(trace_id))
        return result

    def log_llm_call_sync(
        self,
        trace_id: str,
        name: str,
        model: str,
        input_data: Any,
        output: Any,
        *,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self._sync(self.log_llm_call(trace_id, name, model, input_data, output, usage=usage))

    def score_sync(
        self,
        trace_id: str,
        name: str,
        value: float,
        *,
        comment: str | None = None,
    ) -> None:
        self._sync(self.score(trace_id, name, value, comment=comment))

    def get_sessions_sync(self, limit: int = 10) -> str:
        return str(self._sync(self.get_sessions(limit)))

    def flush_sync(self) -> None:
        self._sync(self.flush())
