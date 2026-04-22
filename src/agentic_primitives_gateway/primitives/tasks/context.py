"""Per-request task-board context.

Task-board handlers (``create_task``, ``claim_task``, ...) read the
current team run id and the acting agent's role from contextvars
instead of taking them as handler params.  The team runner sets these
values before each planner/worker/synthesizer invocation.

Separate module from the ``tasks`` primitive provider because the
context is set by the *team* runner (which composes many agents over
one task board), not by the task primitive itself.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

_team_run_id: ContextVar[str | None] = ContextVar("apg_tasks_team_run_id", default=None)
_agent_role: ContextVar[str | None] = ContextVar("apg_tasks_agent_role", default=None)


# ── Team run id ──────────────────────────────────────────────────────


def set_team_run_id(team_run_id: str | None) -> Token:
    return _team_run_id.set(team_run_id)


def get_team_run_id() -> str | None:
    return _team_run_id.get()


def reset_team_run_id(token: Token) -> None:
    _team_run_id.reset(token)


# ── Agent role (planner / synthesizer / worker name) ─────────────────


def set_agent_role(role: str | None) -> Token:
    return _agent_role.set(role)


def get_agent_role() -> str | None:
    return _agent_role.get()


def reset_agent_role(token: Token) -> None:
    _agent_role.reset(token)
