from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from agentic_primitives_gateway.models.enums import CodeLanguage, SessionStatus
from agentic_primitives_gateway.primitives.code_interpreter.base import CodeInterpreterProvider

logger = logging.getLogger(__name__)


class NoopCodeInterpreterProvider(CodeInterpreterProvider):
    """No-op code interpreter provider that tracks sessions but doesn't execute code."""

    def __init__(self, **kwargs: Any) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        logger.info("NoopCodeInterpreterProvider initialized")

    async def start_session(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        sid = session_id or "noop-session"
        language = (config or {}).get("language", CodeLanguage.PYTHON)
        now = datetime.now(UTC).isoformat()
        self._sessions[sid] = {
            "session_id": sid,
            "status": SessionStatus.ACTIVE,
            "language": language,
            "created_at": now,
        }
        logger.debug("noop start_session: %s", sid)
        return dict(self._sessions[sid])

    async def stop_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
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
        sessions = list(self._sessions.values())
        if status:
            sessions = [s for s in sessions if s.get("status") == status]
        return sessions

    async def get_session(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        return dict(session)

    async def get_execution_history(
        self,
        session_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if session_id not in self._sessions:
            raise KeyError(f"Session {session_id} not found")
        return []
