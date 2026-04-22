"""Per-request browser session-id context.

Tool handlers (``navigate``, ``click``, ``read_page``, ...) read the
current browser session from this contextvar instead of taking
``session_id`` as a handler param.  The runner sets the value after
``start_session`` succeeds, and clears it on session cleanup.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_browser_session_id: ContextVar[str | None] = ContextVar("apg_browser_session_id", default=None)


def set_browser_session_id(session_id: str | None) -> Token:
    return _browser_session_id.set(session_id)


def get_browser_session_id() -> str | None:
    return _browser_session_id.get()


def reset_browser_session_id(token: Token) -> None:
    _browser_session_id.reset(token)
