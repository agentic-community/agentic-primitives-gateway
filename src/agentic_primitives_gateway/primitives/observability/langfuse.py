from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from langfuse import Langfuse

from agentic_primitives_gateway.context import get_service_credentials_or_defaults
from agentic_primitives_gateway.models.enums import LogLevel
from agentic_primitives_gateway.primitives.observability.base import ObservabilityProvider

logger = logging.getLogger(__name__)

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

    def _resolve_client(self) -> Langfuse:
        """Resolve Langfuse client from context. Must be called from async context."""
        creds = get_service_credentials_or_defaults(
            "langfuse",
            {
                "public_key": self._default_public_key,
                "secret_key": self._default_secret_key,
                "base_url": self._default_base_url,
            },
        )

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
            with client.start_as_current_observation(
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
