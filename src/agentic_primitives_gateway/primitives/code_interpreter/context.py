"""Per-request code-interpreter session-id context.

``execute_code`` reads the current code-interpreter session from this
contextvar instead of taking ``session_id`` as a handler param.  The
runner sets the value after ``start_session`` succeeds, and clears it
on session cleanup.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_code_interpreter_session_id: ContextVar[str | None] = ContextVar("apg_code_interpreter_session_id", default=None)


def set_code_interpreter_session_id(session_id: str | None) -> Token:
    return _code_interpreter_session_id.set(session_id)


def get_code_interpreter_session_id() -> str | None:
    return _code_interpreter_session_id.get()


def reset_code_interpreter_session_id(token: Token) -> None:
    _code_interpreter_session_id.reset(token)
