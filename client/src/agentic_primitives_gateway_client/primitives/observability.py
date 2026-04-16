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

    def _get_sync_http(self) -> Any:
        """Get or create a sync httpx.Client for sync wrappers.

        Using the async client from a new event loop causes 'bound to a
        different event loop' errors when called after frameworks like
        Strands that run their own event loop.
        """
        if getattr(self, "_sync_http", None) is None:
            import httpx

            self._sync_http = httpx.Client(
                base_url=str(self._client._client.base_url),
                timeout=30,
            )
        return self._sync_http

    def _sync_post(self, path: str, json: dict[str, Any]) -> None:
        """Fire-and-forget sync POST (best-effort)."""
        try:
            http = self._get_sync_http()
            headers = dict(self._client._headers)
            http.post(path, json=json, headers=headers)
        except Exception:
            pass

    def trace_sync(
        self,
        name: str,
        input_data: dict[str, Any],
        output: str,
        tags: list[str] | None = None,
    ) -> None:
        self._sync_post(
            "/api/v1/observability/traces",
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
            },
        )

    def log_sync(self, level: str, message: str, **extra: str) -> None:
        self._sync_post(
            "/api/v1/observability/logs",
            {"level": level, "message": message, **extra},
        )

    def _sync_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Sync GET returning parsed JSON."""
        try:
            http = self._get_sync_http()
            headers = dict(self._client._headers)
            resp = http.get(path, params=params, headers=headers)
            return resp.json()  # type: ignore[no-any-return]
        except Exception:
            return {}

    def query_traces_sync(self, limit: int = 10) -> str:
        result = self._sync_get("/api/v1/observability/traces", {"limit": limit})
        traces = result.get("traces", [])
        if not traces:
            return "No traces found."
        lines = [f"  [{t.get('trace_id', '?')}] {t.get('name', '?')}" for t in traces[:limit]]
        return f"{len(lines)} traces:\n" + "\n".join(lines)

    def get_trace_sync(self, trace_id: str) -> dict[str, Any]:
        return self._sync_get(f"/api/v1/observability/traces/{trace_id}")

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
        self._sync_post(
            f"/api/v1/observability/traces/{trace_id}/generations",
            {
                "name": name,
                "model": model,
                "input": input_data,
                "output": output,
                "usage": usage,
                "metadata": {"session": self.session_id},
            },
        )

    def score_sync(
        self,
        trace_id: str,
        name: str,
        value: float,
        *,
        comment: str | None = None,
    ) -> None:
        body: dict[str, Any] = {"name": name, "value": value}
        if comment:
            body["comment"] = comment
        self._sync_post(f"/api/v1/observability/traces/{trace_id}/scores", body)

    def get_sessions_sync(self, limit: int = 10) -> str:
        result = self._sync_get("/api/v1/observability/sessions", {"limit": limit})
        sessions = result.get("sessions", [])
        if not sessions:
            return "No sessions found."
        return str(sessions[:limit])

    def flush_sync(self) -> None:
        self._sync_post("/api/v1/observability/flush", {})
