import contextlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.responses import StreamingResponse

from agentic_primitives_gateway.agents.team_runner import TeamRunner
from agentic_primitives_gateway.agents.team_store import TeamStore
from agentic_primitives_gateway.auth.access import require_access, require_owner_or_admin
from agentic_primitives_gateway.models.teams import (
    CreateTeamRequest,
    TeamListResponse,
    TeamRunRequest,
    TeamRunResponse,
    TeamSpec,
    UpdateTeamRequest,
)
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.routes._background import BackgroundRunManager, reconnect_event_generator, sse_response
from agentic_primitives_gateway.routes._helpers import require_principal

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/teams", tags=["teams"])

_store: TeamStore | None = None
_runner = TeamRunner()
_bg = BackgroundRunManager(stale_seconds=600, grace_seconds=60)
_active_team_runs = _bg.runs


def set_team_bg(bg: BackgroundRunManager) -> None:
    """Replace the background run manager (called during app lifespan)."""
    global _bg, _active_team_runs
    _bg = bg
    _active_team_runs = bg.runs


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
    principal = require_principal()
    data = request.model_dump()
    data["owner_id"] = principal.id
    spec = TeamSpec(**data)
    existing = await store.get(spec.name)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Team '{spec.name}' already exists")
    return await store.create(spec)


@router.get("", response_model=TeamListResponse)
async def list_teams() -> TeamListResponse:
    store = _get_store()
    principal = require_principal()
    teams = await store.list_for_user(principal)
    return TeamListResponse(teams=teams)


@router.get("/{name}", response_model=TeamSpec)
async def get_team(name: str) -> TeamSpec:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(require_principal(), spec.owner_id, spec.shared_with)
    return spec


@router.put("/{name}", response_model=TeamSpec)
async def update_team(name: str, request: UpdateTeamRequest) -> TeamSpec:
    store = _get_store()
    existing = await store.get(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_owner_or_admin(require_principal(), existing.owner_id)
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    return await store.update(name, updates)


@router.delete("/{name}")
async def delete_team(name: str) -> dict[str, str]:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_owner_or_admin(require_principal(), spec.owner_id)
    await store.delete(name)
    return {"status": "deleted"}


@router.post("/{name}/run", response_model=TeamRunResponse)
async def run_team(name: str, request: TeamRunRequest) -> TeamRunResponse:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(require_principal(), spec.owner_id, spec.shared_with)

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
    require_access(require_principal(), spec.owner_id, spec.shared_with)

    # Use a placeholder key since team_run_id is generated inside the runner.
    # The background task re-keys automatically when it sees team_run_id.
    placeholder = f"__pending_{id(spec)}"
    queue, _ = _bg.start(
        placeholder,
        _runner.run_stream(team_spec=spec, message=request.message),
        owner_id=require_principal().id,
        record_events=True,
        rekey_field="team_run_id",
        index_key=f"team:{name}:runs",
    )
    return sse_response(queue)


@router.get("/{name}/runs")
async def list_team_runs(name: str) -> dict:
    """List all known runs for a team.

    Checks both in-memory active runs and a per-team Redis index for
    runs associated with this team.
    """
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(require_principal(), spec.owner_id, spec.shared_with)

    runs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # Check local active runs
    for key, (task, _, event_log, _started) in _bg.runs.items():
        if key.startswith("__pending_"):
            continue
        for ev in event_log:
            if ev.get("type") == "team_start" and ev.get("team_name") == name:
                status = "running" if not task.done() else "idle"
                runs.append({"team_run_id": key, "status": status})
                seen_ids.add(key)
                break

    # Check per-team index in Redis (O(1) instead of SCAN)
    if _bg._event_store:
        try:
            indexed_ids = await _bg._event_store.get_index(f"team:{name}:runs")
            for run_id in indexed_ids:
                if run_id in seen_ids:
                    continue
                status = await _bg.get_status_async(run_id)
                runs.append({"team_run_id": run_id, "status": status})
        except Exception:
            logger.debug("Failed to read team run index from Redis", exc_info=True)

    return {"team_name": name, "runs": runs}


async def _require_run_owner(team_run_id: str) -> None:
    """Raise 403 if the current principal does not own the run."""
    owner = await _bg.get_owner_async(team_run_id)
    if owner and owner != require_principal().id and not require_principal().is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.delete("/{name}/runs/{team_run_id}")
async def delete_team_run(name: str, team_run_id: str) -> dict:
    """Delete a team run's data (tasks, events)."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(require_principal(), spec.owner_id, spec.shared_with)
    await _require_run_owner(team_run_id)

    # Clean up task board
    try:
        all_tasks = await registry.tasks.list_tasks(team_run_id)
        for t in all_tasks:
            await registry.tasks.update_task(team_run_id, t.id, status="failed", result="Run deleted")
    except (NotImplementedError, Exception):
        pass

    # Clean up events in Redis
    if _bg._event_store:
        with contextlib.suppress(Exception):
            await _bg._event_store.delete(team_run_id)

    # Remove from local tracking
    _bg.runs.pop(team_run_id, None)

    return {"status": "deleted"}


@router.get("/{name}/runs/{team_run_id}/stream")
async def stream_team_run_events(name: str, team_run_id: str) -> StreamingResponse:
    """SSE stream of team run events from the event store.

    Replays all existing events, then polls for new events every second
    until the run completes. Used by the UI to reconnect after a stream
    drop (e.g. server restart with checkpoint recovery).
    """
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(require_principal(), spec.owner_id, spec.shared_with)
    await _require_run_owner(team_run_id)

    return StreamingResponse(
        reconnect_event_generator(_bg, team_run_id, done_event_types=frozenset({"done", "cancelled"})),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{name}/runs/{team_run_id}/status")
async def get_team_run_status(name: str, team_run_id: str) -> dict:
    """Check if a team run is currently active."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(require_principal(), spec.owner_id, spec.shared_with)
    await _require_run_owner(team_run_id)

    _bg.cleanup()
    return {"status": await _bg.get_status_async(team_run_id)}


@router.delete("/{name}/runs/{team_run_id}/cancel")
async def cancel_team_run(name: str, team_run_id: str) -> dict:
    """Cancel an active team run and mark all in-progress tasks as failed.

    Works for both locally-tracked runs (via BackgroundRunManager) and
    recovered runs (which bypass the manager). For recovered runs, does
    a "soft cancel" — marks tasks as failed and deletes the checkpoint
    so the run stops naturally.
    """
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(require_principal(), spec.owner_id, spec.shared_with)
    await _require_run_owner(team_run_id)

    # Signal the team runner to stop workers at the next checkpoint
    _runner.cancel_run(team_run_id)

    # Try to cancel the local background task (works for non-recovered runs)
    await _bg.cancel(team_run_id)

    # Also try to cancel a recovery task (works for resumed runs on this replica)
    from agentic_primitives_gateway.agents.checkpoint import cancel_recovery_task

    cancel_recovery_task(team_run_id)

    # Always mark tasks as failed — works for both local and recovered runs
    try:
        all_tasks = await registry.tasks.list_tasks(team_run_id)
        for t in all_tasks:
            if t.status in ("pending", "in_progress"):
                await registry.tasks.update_task(team_run_id, t.id, status="failed", result="Run cancelled")
    except (NotImplementedError, Exception):
        pass

    # Set event store status to cancelled
    if _bg._event_store:
        with contextlib.suppress(Exception):
            await _bg._event_store.set_status(team_run_id, "cancelled")
            await _bg._event_store.append_event(team_run_id, {"type": "cancelled"})

    # Delete checkpoint so orphan recovery doesn't resume this run
    from agentic_primitives_gateway.routes.agents import _runner as agent_runner

    if agent_runner._checkpoint_store:
        principal = require_principal()
        with contextlib.suppress(Exception):
            await agent_runner._checkpoint_store.delete(f"{principal.id}:{team_run_id}")
        # Signal cross-replica cancellation for recovered runs on other replicas
        with contextlib.suppress(Exception):
            await agent_runner._checkpoint_store.mark_cancelled(team_run_id)

    return {"status": "cancelled"}


@router.get("/{name}/runs/{team_run_id}/events")
async def get_team_run_events(name: str, team_run_id: str) -> dict:
    """Return all recorded SSE events for a team run (for UI replay)."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(require_principal(), spec.owner_id, spec.shared_with)
    await _require_run_owner(team_run_id)

    _bg.cleanup()
    status = await _bg.get_status_async(team_run_id)
    events = await _bg.get_events_async(team_run_id)
    if not events and status == "idle":
        return {"team_run_id": team_run_id, "status": "unknown", "events": []}
    return {
        "team_run_id": team_run_id,
        "status": status,
        "events": events,
    }


@router.get("/{name}/runs/{team_run_id}")
async def get_team_run(name: str, team_run_id: str) -> dict:
    """Retrieve task board state for a completed or in-progress team run."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(require_principal(), spec.owner_id, spec.shared_with)
    await _require_run_owner(team_run_id)

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
