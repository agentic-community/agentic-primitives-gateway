from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.primitives.browser.base import BrowserProvider

logger = logging.getLogger(__name__)


class NoopBrowserProvider(BrowserProvider):
    """No-op browser provider that returns placeholder values."""

    def __init__(self, **kwargs: Any) -> None:
        logger.info("NoopBrowserProvider initialized")

    async def start_session(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.debug("noop start_session: %s", session_id)
        return {
            "session_id": session_id or "noop-session",
            "status": "active",
        }

    async def stop_session(self, session_id: str) -> None:
        logger.debug("noop stop_session: %s", session_id)

    async def get_session(self, session_id: str) -> dict[str, Any]:
        logger.debug("noop get_session: %s", session_id)
        return {"session_id": session_id, "status": "active"}

    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        logger.debug("noop list_sessions: %s", status)
        return []

    async def get_live_view_url(self, session_id: str, expires: int = 300) -> str:
        logger.debug("noop get_live_view_url: %s", session_id)
        return ""
