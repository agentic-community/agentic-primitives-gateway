from __future__ import annotations

import asyncio
import logging
import re
import uuid
from functools import partial
from typing import Any

import httpx
from langfuse import Langfuse

from agentic_primitives_gateway.context import get_service_credentials_or_defaults
from agentic_primitives_gateway.models.enums import LogLevel
from agentic_primitives_gateway.primitives.observability.base import ObservabilityProvider

logger = logging.getLogger(__name__)

_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


def _to_langfuse_trace_id(trace_id: str) -> str:
    """Convert a trace ID to a Langfuse v3 compatible 32-char lowercase hex string.

    If the input is already valid 32-char hex, return as-is.
    Otherwise, generate a deterministic 32-char hex via uuid5.
    """
    if _HEX32_RE.match(trace_id):
        return trace_id
    return uuid.uuid5(uuid.NAMESPACE_URL, trace_id).hex


_LEVEL_MAP = {
    LogLevel.DEBUG: "DEBUG",
    LogLevel.INFO: "DEFAULT",
    LogLevel.WARNING: "WARNING",
    LogLevel.ERROR: "ERROR",
}


class LangfuseObservabilityProvider(ObservabilityProvider):
    """Observability provider backed by Langfuse.

    Langfuse credentials (public_key, secret_key, base_url) are read from
    request context on every call via the X-Cred-Langfuse-* headers. This
    allows each agent to use its own Langfuse project.

    If no credentials are in the request context, falls back to config-level
    defaults (useful for shared/platform-owned Langfuse projects).

    Provider config example::

        backend: agentic_primitives_gateway.primitives.observability.langfuse.LangfuseObservabilityProvider
        config:
          # Optional fallback credentials (used when client doesn't send any)
          public_key: "pk-..."
          secret_key: "sk-..."
          base_url: "https://cloud.langfuse.com"
    """

    def __init__(
        self,
        public_key: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        import os

        # Server-side defaults: provider config → env vars
        self._default_public_key = public_key or os.environ.get("LANGFUSE_PUBLIC_KEY")
        self._default_secret_key = secret_key or os.environ.get("LANGFUSE_SECRET_KEY")
        self._default_base_url = base_url or os.environ.get("LANGFUSE_BASE_URL")
        logger.info("Langfuse observability provider initialized")

    def _resolve_credentials(self) -> dict[str, Any]:
        """Resolve Langfuse credentials from context."""
        return get_service_credentials_or_defaults(
            "langfuse",
            {
                "public_key": self._default_public_key,
                "secret_key": self._default_secret_key,
                "base_url": self._default_base_url,
            },
        )

    def _resolve_client(self) -> Langfuse:
        """Resolve Langfuse client from context. Must be called from async context."""
        creds = self._resolve_credentials()

        return Langfuse(
            public_key=creds.get("public_key"),
            secret_key=creds.get("secret_key"),
            base_url=creds.get("base_url"),
        )

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))

    async def ingest_trace(self, trace: dict[str, Any]) -> None:
        client = self._resolve_client()

        def _ingest() -> None:
            raw_trace_id = trace.get("trace_id", uuid.uuid4().hex)
            lf_trace_id = _to_langfuse_trace_id(raw_trace_id)

            with client.start_as_current_observation(
                trace_context={"trace_id": lf_trace_id},
                as_type="span",
                name=trace.get("name", trace.get("trace_id", "trace")),
            ) as root:
                root.update_trace(
                    name=trace.get("name"),
                    user_id=trace.get("user_id"),
                    session_id=trace.get("session_id"),
                    metadata=trace.get("metadata"),
                    tags=trace.get("tags"),
                    input=trace.get("input"),
                    output=trace.get("output"),
                )
                root.update(
                    input=trace.get("input"),
                    output=trace.get("output"),
                    metadata=trace.get("metadata"),
                )

                for span_data in trace.get("spans", []):
                    with root.start_as_current_observation(
                        as_type="span",
                        name=span_data.get("name", "span"),
                    ) as child:
                        child.update(
                            input=span_data.get("input"),
                            output=span_data.get("output"),
                            metadata=span_data.get("metadata"),
                            level=span_data.get("level"),
                            model=span_data.get("model"),
                        )

            client.flush()

        await self._run_sync(_ingest)

    async def ingest_log(self, log_entry: dict[str, Any]) -> None:
        client = self._resolve_client()

        def _ingest() -> None:
            level = _LEVEL_MAP.get(log_entry.get("level", "info").lower(), "DEFAULT")

            with client.start_as_current_observation(
                as_type="span",
                name="log",
            ) as root:
                root.create_event(
                    name=log_entry.get("message", "log"),
                    input={"message": log_entry.get("message", "")},
                    metadata=log_entry.get("metadata"),
                    level=level,  # type: ignore[arg-type]
                )

            client.flush()

        await self._run_sync(_ingest)

    async def query_traces(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        client = self._resolve_client()

        def _query() -> list[dict[str, Any]]:
            f = filters or {}
            kwargs: dict[str, Any] = {}
            if f.get("trace_id"):
                try:
                    t = client.api.trace.get(f["trace_id"])
                    return [_trace_to_dict(t)]
                except Exception:
                    return []

            if f.get("name"):
                kwargs["name"] = f["name"]
            if f.get("user_id"):
                kwargs["user_id"] = f["user_id"]
            if f.get("session_id"):
                kwargs["session_id"] = f["session_id"]
            if f.get("tags"):
                kwargs["tags"] = f["tags"]
            kwargs["limit"] = f.get("limit", 100)

            result = client.api.trace.list(**kwargs)
            return [_trace_to_dict(t) for t in result.data]

        result: list[dict[str, Any]] = await self._run_sync(_query)
        return result

    # ── Trace retrieval & LLM generation ────────────────────────────

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        client = self._resolve_client()

        def _get() -> dict[str, Any]:
            try:
                t = client.api.trace.get(trace_id)
                return _trace_to_dict(t)
            except Exception:
                raise KeyError(f"Trace not found: {trace_id}") from None

        result: dict[str, Any] = await self._run_sync(_get)
        return result

    async def log_generation(
        self,
        trace_id: str,
        name: str,
        model: str,
        input: Any = None,
        output: Any = None,
        *,
        usage: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
    ) -> dict[str, Any]:
        client = self._resolve_client()

        def _log() -> dict[str, Any]:
            lf_trace_id = _to_langfuse_trace_id(trace_id)
            kwargs: dict[str, Any] = {
                "trace_context": {"trace_id": lf_trace_id},
                "name": name,
                "model": model,
            }
            if input is not None:
                kwargs["input"] = input
            if output is not None:
                kwargs["output"] = output
            if usage:
                kwargs["usage"] = usage
            if metadata:
                kwargs["metadata"] = metadata
            if level:
                lf_level = _LEVEL_MAP.get(level.lower(), "DEFAULT")  # type: ignore[call-overload]
                kwargs["level"] = lf_level
            gen = client.start_generation(**kwargs)
            gen.end()
            client.flush()
            return {
                "generation_id": getattr(gen, "id", None),
                "trace_id": trace_id,
                "name": name,
                "model": model,
            }

        result: dict[str, Any] = await self._run_sync(_log)
        return result

    async def flush(self) -> None:
        client = self._resolve_client()
        await self._run_sync(client.flush)

    # ── Trace updates & scoring ──────────────────────────────────────

    async def update_trace(
        self,
        trace_id: str,
        *,
        name: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        input: Any = None,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        client = self._resolve_client()

        def _update() -> dict[str, Any]:
            lf_trace_id = _to_langfuse_trace_id(trace_id)
            update_kwargs: dict[str, Any] = {}
            if name is not None:
                update_kwargs["name"] = name
            if user_id is not None:
                update_kwargs["user_id"] = user_id
            if session_id is not None:
                update_kwargs["session_id"] = session_id
            if input is not None:
                update_kwargs["input"] = input
            if output is not None:
                update_kwargs["output"] = output
            if metadata is not None:
                update_kwargs["metadata"] = metadata
            if tags is not None:
                update_kwargs["tags"] = tags
            with client.start_as_current_observation(
                trace_context={"trace_id": lf_trace_id},
                name="update",
                as_type="span",
            ):
                client.update_current_trace(**update_kwargs)
            client.flush()
            return {"trace_id": trace_id, "status": "updated"}

        result: dict[str, Any] = await self._run_sync(_update)
        return result

    async def score_trace(
        self,
        trace_id: str,
        name: str,
        value: float,
        *,
        comment: str | None = None,
        data_type: str | None = None,
    ) -> dict[str, Any]:
        client = self._resolve_client()

        def _score() -> dict[str, Any]:
            lf_trace_id = _to_langfuse_trace_id(trace_id)
            kwargs: dict[str, Any] = {
                "trace_id": lf_trace_id,
                "name": name,
                "value": value,
            }
            if comment is not None:
                kwargs["comment"] = comment
            if data_type is not None:
                kwargs["data_type"] = data_type
            client.create_score(**kwargs)
            client.flush()
            return {
                "score_id": None,
                "trace_id": trace_id,
                "name": name,
                "value": value,
            }

        result: dict[str, Any] = await self._run_sync(_score)
        return result

    async def list_scores(self, trace_id: str) -> list[dict[str, Any]]:
        creds = self._resolve_credentials()

        def _list() -> list[dict[str, Any]]:
            lf_trace_id = _to_langfuse_trace_id(trace_id)
            base_url = (creds.get("base_url") or "https://cloud.langfuse.com").rstrip("/")
            url = f"{base_url}/api/public/scores?traceId={lf_trace_id}"
            resp = httpx.get(
                url,
                auth=(creds.get("public_key", ""), creds.get("secret_key", "")),
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            return [
                {
                    "score_id": s.get("id"),
                    "trace_id": s.get("traceId", trace_id),
                    "name": s.get("name", ""),
                    "value": s.get("value", 0),
                    "comment": s.get("comment"),
                    "data_type": s.get("dataType"),
                }
                for s in data
            ]

        scores: list[dict[str, Any]] = await self._run_sync(_list)
        return scores

    # ── Session management ───────────────────────────────────────────

    async def list_sessions(
        self,
        *,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        client = self._resolve_client()

        def _list() -> list[dict[str, Any]]:
            result = client.api.sessions.list(page=1, limit=limit)
            sessions = [
                {
                    "session_id": getattr(s, "id", ""),
                    "user_id": getattr(s, "user_id", None),
                    "trace_count": getattr(s, "trace_count", 0),
                    "created_at": str(getattr(s, "created_at", "")),
                    "metadata": getattr(s, "metadata", {}) or {},
                }
                for s in result.data
            ]
            if user_id:
                sessions = [s for s in sessions if s.get("user_id") == user_id]
            return sessions

        sessions: list[dict[str, Any]] = await self._run_sync(_list)
        return sessions

    async def get_session(self, session_id: str) -> dict[str, Any]:
        client = self._resolve_client()

        def _get() -> dict[str, Any]:
            s = client.api.sessions.get(session_id)
            return {
                "session_id": getattr(s, "id", session_id),
                "user_id": getattr(s, "user_id", None),
                "trace_count": getattr(s, "trace_count", 0),
                "created_at": str(getattr(s, "created_at", "")),
                "metadata": getattr(s, "metadata", {}) or {},
            }

        result: dict[str, Any] = await self._run_sync(_get)
        return result

    async def healthcheck(self) -> bool:
        try:
            client = self._resolve_client()
            result: bool = await self._run_sync(client.auth_check)
            return result
        except Exception:
            logger.exception("Langfuse healthcheck failed")
            return False


def _trace_to_dict(trace: Any) -> dict[str, Any]:
    """Convert a Langfuse trace object to our standard dict format."""
    result: dict[str, Any] = {
        "trace_id": trace.id,
        "name": getattr(trace, "name", None),
        "user_id": getattr(trace, "user_id", None),
        "session_id": getattr(trace, "session_id", None),
        "input": getattr(trace, "input", None),
        "output": getattr(trace, "output", None),
        "tags": getattr(trace, "tags", []),
        "metadata": getattr(trace, "metadata", {}),
        "latency": getattr(trace, "latency", None),
        "total_cost": getattr(trace, "total_cost", None),
    }

    observations = getattr(trace, "observations", None)
    if observations and not isinstance(observations[0], str):
        result["spans"] = [
            {
                "name": obs.name,
                "input": obs.input,
                "output": obs.output,
                "metadata": obs.metadata,
                "level": obs.level,
                "model": getattr(obs, "model", None),
            }
            for obs in observations
        ]

    return result
