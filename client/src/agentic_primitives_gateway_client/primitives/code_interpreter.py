from __future__ import annotations

import asyncio
from typing import Any

from agentic_primitives_gateway_client.client import AgenticPlatformClient


class CodeInterpreter:
    """Helper for the code interpreter primitive — session lifecycle + execution."""

    def __init__(
        self,
        client: AgenticPlatformClient,
        language: str = "python",
    ) -> None:
        self._client = client
        self._language = language
        self._session_id: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def execute(self, code: str, language: str | None = None) -> str:
        """Execute code, starting a session if needed."""
        lang = language or self._language
        if self._session_id is None:
            session = await self._client.start_code_session(language=lang)
            self._session_id = session["session_id"]

        result = await self._client.execute_code(self._session_id, code=code, language=lang)
        parts = []
        if result.get("stdout"):
            parts.append(f"stdout:\n{result['stdout']}")
        if result.get("stderr"):
            parts.append(f"stderr:\n{result['stderr']}")
        if result.get("error"):
            parts.append(f"error: {result['error']}")
        if not parts:
            parts.append("(no output)")
        return "\n".join(parts)

    async def close(self) -> None:
        """Stop the current session if one is active."""
        if self._session_id:
            try:  # noqa: SIM105
                await self._client.stop_code_session(self._session_id)
            except Exception:
                pass
            self._session_id = None

    # ── Extended async interface ─────────────────────────────────────

    async def get_session(self) -> dict[str, Any]:
        """Get details for the current session."""
        if not self._session_id:
            return {"error": "No active session"}
        return await self._client.get_code_session(self._session_id)

    async def history(self, limit: int = 10) -> str:
        """Get execution history for the current session, returning formatted string."""
        if not self._session_id:
            return "No active session."
        try:
            result = await self._client.get_execution_history(self._session_id, limit=limit)
            entries = result.get("entries", [])
            if not entries:
                return "No execution history."
            lines: list[str] = []
            for e in entries:
                code_preview = e.get("code", "")[:50]
                lines.append(f"  [{e.get('timestamp', '?')[:19]}] {code_preview!r} → exit={e.get('exit_code', 0)}")
            return f"{len(entries)} executions:\n" + "\n".join(lines)
        except Exception as exc:
            return f"Failed to get history: {exc}"

    # ── Sync wrappers ───────────────────────────────────────────────

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _sync(self, coro: Any) -> Any:
        return self._get_loop().run_until_complete(coro)

    def execute_sync(self, code: str, language: str | None = None) -> str:
        return str(self._sync(self.execute(code, language)))

    def close_sync(self) -> None:
        self._sync(self.close())

    def get_session_sync(self) -> dict[str, Any]:
        result: dict[str, Any] = self._sync(self.get_session())
        return result

    def history_sync(self, limit: int = 10) -> str:
        return str(self._sync(self.history(limit)))
