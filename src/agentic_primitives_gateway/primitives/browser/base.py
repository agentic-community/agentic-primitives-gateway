from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BrowserProvider(ABC):
    """Abstract base class for browser automation providers.

    Manages cloud-based browser sessions and provides interaction methods
    (navigate, screenshot, page content, click, type) via CDP.
    """

    @abstractmethod
    async def start_session(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def stop_session(self, session_id: str) -> None: ...

    @abstractmethod
    async def get_session(self, session_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def get_live_view_url(self, session_id: str, expires: int = 300) -> str: ...

    async def navigate(self, session_id: str, url: str) -> dict[str, Any]:
        raise NotImplementedError("navigate not supported by this provider")

    async def screenshot(self, session_id: str) -> str:
        """Take a screenshot. Returns base64-encoded PNG."""
        raise NotImplementedError("screenshot not supported by this provider")

    async def get_page_content(self, session_id: str) -> str:
        """Get the text content of the current page."""
        raise NotImplementedError("get_page_content not supported by this provider")

    async def click(self, session_id: str, selector: str) -> dict[str, Any]:
        raise NotImplementedError("click not supported by this provider")

    async def type_text(self, session_id: str, selector: str, text: str) -> dict[str, Any]:
        raise NotImplementedError("type_text not supported by this provider")

    async def evaluate(self, session_id: str, expression: str) -> Any:
        """Evaluate JavaScript in the browser and return the result."""
        raise NotImplementedError("evaluate not supported by this provider")

    async def healthcheck(self) -> bool | str:
        return True
