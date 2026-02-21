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

    async def healthcheck(self) -> bool:
        return True
