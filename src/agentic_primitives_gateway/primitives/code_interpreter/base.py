from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentic_primitives_gateway.models.enums import CodeLanguage


class CodeInterpreterProvider(ABC):
    """Abstract base class for code interpreter providers.

    Manages sandboxed code execution sessions with file I/O support.

    The ABC auto-wraps ``execute`` / ``upload_file`` / ``download_file``
    on every subclass via ``__init_subclass__`` to emit
    ``code_interpreter.execute`` / ``code_interpreter.file.upload`` /
    ``code_interpreter.file.download`` audit events at the provider
    boundary.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        from agentic_primitives_gateway.primitives.code_interpreter._audit import (
            wrap_download_file,
            wrap_execute,
            wrap_upload_file,
        )

        own = cls.__dict__
        if "execute" in own:
            cls.execute = wrap_execute(own["execute"])  # type: ignore[method-assign]
        if "upload_file" in own:
            cls.upload_file = wrap_upload_file(own["upload_file"])  # type: ignore[method-assign]
        if "download_file" in own:
            cls.download_file = wrap_download_file(own["download_file"])  # type: ignore[method-assign]

    @abstractmethod
    async def start_session(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def stop_session(self, session_id: str) -> None: ...

    @abstractmethod
    async def execute(
        self,
        session_id: str,
        code: str,
        language: str = CodeLanguage.PYTHON,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def upload_file(
        self,
        session_id: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def download_file(self, session_id: str, filename: str) -> bytes: ...

    @abstractmethod
    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]: ...

    async def healthcheck(self) -> bool | str:
        return True

    # ── Session details & execution history (optional) ───────────────

    async def get_session(self, session_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def get_execution_history(
        self,
        session_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError
