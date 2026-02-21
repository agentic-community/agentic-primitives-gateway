from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.models.enums import CodeLanguage, SessionStatus
from agentic_primitives_gateway.primitives.code_interpreter.base import CodeInterpreterProvider

logger = logging.getLogger(__name__)


class NoopCodeInterpreterProvider(CodeInterpreterProvider):
    """No-op code interpreter provider that returns placeholder values."""

    def __init__(self, **kwargs: Any) -> None:
        logger.info("NoopCodeInterpreterProvider initialized")

    async def start_session(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.debug("noop start_session: %s", session_id)
        return {
            "session_id": session_id or "noop-session",
            "status": SessionStatus.ACTIVE,
            "language": (config or {}).get("language", CodeLanguage.PYTHON),
        }

    async def stop_session(self, session_id: str) -> None:
        logger.debug("noop stop_session: %s", session_id)

    async def execute(
        self,
        session_id: str,
        code: str,
        language: str = CodeLanguage.PYTHON,
    ) -> dict[str, Any]:
        logger.debug("noop execute: %s", session_id)
        return {
            "session_id": session_id,
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
        }

    async def upload_file(
        self,
        session_id: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        logger.debug("noop upload_file: %s/%s", session_id, filename)
        return {
            "filename": filename,
            "size": len(content),
            "session_id": session_id,
        }

    async def download_file(self, session_id: str, filename: str) -> bytes:
        logger.debug("noop download_file: %s/%s", session_id, filename)
        return b""

    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        logger.debug("noop list_sessions: %s", status)
        return []
