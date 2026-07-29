from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ObservabilityProvider(ABC):
    """Abstract base class for observability providers.

    The ABC auto-wraps ``ingest_trace`` / ``ingest_log`` / ``update_trace``
    / ``score_trace`` / ``log_generation`` / ``flush`` on every subclass
    via ``__init_subclass__`` to emit ``observability.*`` audit events at
    the provider boundary.  Trace bodies + log payloads are not copied
    into metadata — only identifiers.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        from agentic_primitives_gateway.primitives.observability._audit import (
            wrap_flush,
            wrap_ingest_log,
            wrap_ingest_trace,
            wrap_log_generation,
            wrap_score_trace,
            wrap_update_trace,
        )

        own = cls.__dict__
        if "ingest_trace" in own:
            cls.ingest_trace = wrap_ingest_trace(own["ingest_trace"])  # type: ignore[method-assign]
        if "ingest_log" in own:
            cls.ingest_log = wrap_ingest_log(own["ingest_log"])  # type: ignore[method-assign]
        if "update_trace" in own:
            cls.update_trace = wrap_update_trace(own["update_trace"])  # type: ignore[method-assign]
        if "score_trace" in own:
            cls.score_trace = wrap_score_trace(own["score_trace"])  # type: ignore[method-assign]
        if "log_generation" in own:
            cls.log_generation = wrap_log_generation(own["log_generation"])  # type: ignore[method-assign]
        if "flush" in own:
            cls.flush = wrap_flush(own["flush"])  # type: ignore[method-assign]

    @abstractmethod
    async def ingest_trace(self, trace: dict[str, Any]) -> None: ...

    @abstractmethod
    async def ingest_log(self, log_entry: dict[str, Any]) -> None: ...

    @abstractmethod
    async def query_traces(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...

    async def healthcheck(self) -> bool | str:
        return True

    # ── Trace retrieval & LLM generation (optional) ──────────────────

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        raise NotImplementedError

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
        raise NotImplementedError

    async def flush(self) -> None:
        raise NotImplementedError

    # ── Trace updates & scoring (optional) ───────────────────────────

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
        raise NotImplementedError

    async def score_trace(
        self,
        trace_id: str,
        name: str,
        value: float,
        *,
        comment: str | None = None,
        data_type: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def list_scores(self, trace_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    # ── Session management (optional) ────────────────────────────────

    async def list_sessions(
        self,
        *,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def get_session(self, session_id: str) -> dict[str, Any]:
        raise NotImplementedError
