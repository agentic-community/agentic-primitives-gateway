import asyncio
import contextvars
import json
import logging
import time

from fastapi import APIRouter, HTTPException
from starlette.responses import StreamingResponse

from agentic_primitives_gateway.agents.team_runner import TeamRunner
from agentic_primitives_gateway.agents.team_store import TeamStore
from agentic_primitives_gateway.models.teams import (
    CreateTeamRequest,
    TeamListResponse,
    TeamRunRequest,
    TeamRunResponse,
    TeamSpec,
    UpdateTeamRequest,
)
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/teams", tags=["teams"])

_store: TeamStore | None = None
_runner = TeamRunner()

# Background run tracking: team_run_id -> (task, queue, events, started_at)
_active_team_runs: dict[str, tuple[asyncio.Task, asyncio.Queue, list[dict], float]] = {}
_STALE_RUN_SECONDS = 600


def _cleanup_stale_runs() -> None:
    now = time.monotonic()
    to_remove = [
        rid
        for rid, (task, _, _, started) in _active_team_runs.items()
        if (task.done() and (now - started > 60))  # keep completed runs for 60s for event replay
        or (now - started > _STALE_RUN_SECONDS)
    ]
    for rid in to_remove:
        _active_team_runs.pop(rid, None)


def set_team_store(store: TeamStore) -> None:
    """Set the module-level team store (called during app lifespan)."""
    global _store
    _store = store


def get_team_runner() -> TeamRunner:
    return _runner


def _get_store() -> TeamStore:
    if _store is None:
        raise RuntimeError("Team store not initialized")
    return _store


@router.post("", response_model=TeamSpec, status_code=201)
async def create_team(request: CreateTeamRequest) -> TeamSpec:
    store = _get_store()
    spec = TeamSpec(**request.model_dump())
    existing = await store.get(spec.name)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Team '{spec.name}' already exists")
    return await store.create(spec)


@router.get("", response_model=TeamListResponse)
async def list_teams() -> TeamListResponse:
    store = _get_store()
    teams = await store.list()
    return TeamListResponse(teams=teams)


@router.get("/{name}", response_model=TeamSpec)
async def get_team(name: str) -> TeamSpec:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    return spec


@router.put("/{name}", response_model=TeamSpec)
async def update_team(name: str, request: UpdateTeamRequest) -> TeamSpec:
    store = _get_store()
    existing = await store.get(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    return await store.update(name, updates)


@router.delete("/{name}")
async def delete_team(name: str) -> dict[str, str]:
    store = _get_store()
    deleted = await store.delete(name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    return {"status": "deleted"}


@router.post("/{name}/run", response_model=TeamRunResponse)
async def run_team(name: str, request: TeamRunRequest) -> TeamRunResponse:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")

    return await _runner.run(team_spec=spec, message=request.message)


@router.post("/{name}/run/stream")
async def run_team_stream(name: str, request: TeamRunRequest) -> StreamingResponse:
    """Streaming variant of team run. Returns SSE events.

    The team run executes in a background task so that it completes even
    if the client disconnects mid-stream.
    """
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")

    _cleanup_stale_runs()

    queue: asyncio.Queue[dict | None] = asyncio.Queue()
    event_log: list[dict] = []
    ctx = contextvars.copy_context()
    team_run_id_holder: list[str] = []

    async def _run_in_background() -> None:
        try:
            async for event in _runner.run_stream(team_spec=spec, message=request.message):
                if event.get("type") == "team_start" and event.get("team_run_id"):
                    team_run_id_holder.append(event["team_run_id"])
                event_log.append(event)
                await queue.put(event)
        except Exception as exc:
            err_event = {"type": "error", "detail": str(exc)}
            event_log.append(err_event)
            await queue.put(err_event)
        finally:
            await queue.put(None)

    task = asyncio.create_task(_run_in_background(), context=ctx)

    # We don't know team_run_id yet (it's generated inside the runner),
    # so use a placeholder key until the first event arrives.
    placeholder_key = f"__pending_{id(task)}"
    _active_team_runs[placeholder_key] = (task, queue, event_log, time.monotonic())

    async def event_generator():
        real_key_set = False
        while True:
            event = await queue.get()
            if event is None:
                break
            # Once we see team_run_id, re-key the active runs dict
            if not real_key_set and team_run_id_holder:
                nonlocal placeholder_key
                entry = _active_team_runs.pop(placeholder_key, None)
                if entry:
                    _active_team_runs[team_run_id_holder[0]] = entry
                real_key_set = True
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{name}/runs/{team_run_id}/status")
async def get_team_run_status(name: str, team_run_id: str) -> dict:
    """Check if a team run is currently active."""
    _cleanup_stale_runs()
    entry = _active_team_runs.get(team_run_id)
    if entry and not entry[0].done():
        return {"status": "running"}
    return {"status": "idle"}


@router.get("/{name}/runs/{team_run_id}/events")
async def get_team_run_events(name: str, team_run_id: str) -> dict:
    """Return all SSE events recorded for a team run.

    Allows the UI to replay events and reconstruct full state
    (task board, activity log, streaming content, response).
    """
    _cleanup_stale_runs()
    entry = _active_team_runs.get(team_run_id)
    if entry is None:
        return {"team_run_id": team_run_id, "status": "unknown", "events": []}

    task, _, event_log, _ = entry
    return {
        "team_run_id": team_run_id,
        "status": "running" if not task.done() else "idle",
        "events": event_log,
    }


@router.get("/{name}/runs/{team_run_id}")
async def get_team_run(name: str, team_run_id: str) -> dict:
    """Retrieve task board state for a completed or in-progress team run."""
    try:
        all_tasks = await registry.tasks.list_tasks(team_run_id)
    except (NotImplementedError, Exception):
        all_tasks = []

    tasks_out = []
    for t in all_tasks:
        tasks_out.append(
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "assigned_to": t.assigned_to,
                "suggested_worker": t.suggested_worker,
                "result": t.result,
                "priority": t.priority,
            }
        )

    done_count = sum(1 for t in all_tasks if t.status == "done")
    entry = _active_team_runs.get(team_run_id)
    is_running = entry is not None and not entry[0].done()

    return {
        "team_run_id": team_run_id,
        "team_name": name,
        "status": "running" if is_running else "idle",
        "tasks": tasks_out,
        "tasks_created": len(all_tasks),
        "tasks_completed": done_count,
    }
