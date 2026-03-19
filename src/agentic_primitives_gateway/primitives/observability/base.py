from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ObservabilityProvider(ABC):
    """Abstract base class for observability providers."""

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
