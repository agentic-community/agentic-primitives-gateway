import contextlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from starlette.responses import StreamingResponse

from agentic_primitives_gateway.agents.team_runner import TeamRunner
from agentic_primitives_gateway.agents.team_store import TeamStore
from agentic_primitives_gateway.auth.access import require_access, require_owner_or_admin
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import get_authenticated_principal
from agentic_primitives_gateway.models.teams import (
    CreateTeamRequest,
    TeamListResponse,
    TeamRunRequest,
    TeamRunResponse,
    TeamSpec,
    UpdateTeamRequest,
)
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.routes._background import BackgroundRunManager, sse_response

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


def _principal() -> AuthenticatedPrincipal:
    """Return the authenticated principal. Raises if not set."""
    principal = get_authenticated_principal()
    if principal is None:
        raise RuntimeError("No authenticated principal — auth middleware did not run")
    return principal


@router.post("", response_model=TeamSpec, status_code=201)
async def create_team(request: CreateTeamRequest) -> TeamSpec:
    store = _get_store()
    principal = _principal()
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
    principal = _principal()
    teams = await store.list_for_user(principal)
    return TeamListResponse(teams=teams)


@router.get("/{name}", response_model=TeamSpec)
async def get_team(name: str) -> TeamSpec:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)
    return spec


@router.put("/{name}", response_model=TeamSpec)
async def update_team(name: str, request: UpdateTeamRequest) -> TeamSpec:
    store = _get_store()
    existing = await store.get(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_owner_or_admin(_principal(), existing.owner_id)
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    return await store.update(name, updates)


@router.delete("/{name}")
async def delete_team(name: str) -> dict[str, str]:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_owner_or_admin(_principal(), spec.owner_id)
    await store.delete(name)
    return {"status": "deleted"}


@router.post("/{name}/run", response_model=TeamRunResponse)
async def run_team(name: str, request: TeamRunRequest) -> TeamRunResponse:
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)

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
    require_access(_principal(), spec.owner_id, spec.shared_with)

    # Use a placeholder key since team_run_id is generated inside the runner.
    # The background task re-keys automatically when it sees team_run_id.
    placeholder = f"__pending_{id(spec)}"
    queue, _ = _bg.start(
        placeholder,
        _runner.run_stream(team_spec=spec, message=request.message),
        owner_id=_principal().id,
        record_events=True,
        rekey_field="team_run_id",
    )
    return sse_response(queue)


@router.get("/{name}/runs")
async def list_team_runs(name: str) -> dict:
    """List all known runs for a team.

    Checks both in-memory active runs and Redis event store for
    runs associated with this team.
    """
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)

    runs: list[dict[str, Any]] = []

    # Check local active runs
    for key, (task, _, event_log, _started) in _bg.runs.items():
        if key.startswith("__pending_"):
            continue
        # Check if this run belongs to this team by looking at events
        for ev in event_log:
            if ev.get("type") == "team_start" and ev.get("team_name") == name:
                status = "running" if not task.done() else "idle"
                runs.append({"team_run_id": key, "status": status})
                break

    # Check Redis event store for completed runs
    if _bg._event_store:
        try:
            # Scan for run events that mention this team

            redis_client = _bg._event_store._redis  # type: ignore[attr-defined]
            async for redis_key in redis_client.scan_iter(match="run:*:events"):
                run_id = redis_key.removeprefix("run:").removesuffix(":events")
                if any(r["team_run_id"] == run_id for r in runs):
                    continue  # already found locally
                events = await _bg.get_events_async(run_id)
                for ev in events:
                    if ev.get("type") == "team_start" and ev.get("team_name") == name:
                        status = await _bg.get_status_async(run_id)
                        runs.append({"team_run_id": run_id, "status": status})
                        break
        except Exception:
            logger.debug("Failed to scan Redis for team runs", exc_info=True)

    return {"team_name": name, "runs": runs}


async def _require_run_owner(team_run_id: str) -> None:
    """Raise 403 if the current principal does not own the run."""
    owner = await _bg.get_owner_async(team_run_id)
    if owner and owner != _principal().id and not _principal().is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.delete("/{name}/runs/{team_run_id}")
async def delete_team_run(name: str, team_run_id: str) -> dict:
    """Delete a team run's data (tasks, events)."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)
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
    import asyncio as _asyncio
    import json as _json

    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)
    await _require_run_owner(team_run_id)

    async def _generate():
        sent = 0
        idle_count = 0
        max_idle = 90  # Stop after 90s of no new events

        while idle_count < max_idle:
            events = await _bg.get_events_async(team_run_id)
            status = await _bg.get_status_async(team_run_id)

            # Send any new events since last check
            if len(events) > sent:
                for event in events[sent:]:
                    yield f"data: {_json.dumps(event, default=str)}\n\n"
                sent = len(events)
                idle_count = 0
            else:
                idle_count += 1

            # If the run is done, send remaining events and close
            if status == "idle" and len(events) > 0 and idle_count > 3:
                break

            await _asyncio.sleep(1)

    return StreamingResponse(
        _generate(),
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
    require_access(_principal(), spec.owner_id, spec.shared_with)
    await _require_run_owner(team_run_id)

    _bg.cleanup()
    return {"status": await _bg.get_status_async(team_run_id)}


@router.delete("/{name}/runs/{team_run_id}/cancel")
async def cancel_team_run(name: str, team_run_id: str) -> dict:
    """Cancel an active team run."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)
    await _require_run_owner(team_run_id)

    cancelled = await _bg.cancel(team_run_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="No active run found")
    return {"status": "cancelled"}


@router.get("/{name}/runs/{team_run_id}/events")
async def get_team_run_events(name: str, team_run_id: str) -> dict:
    """Return all recorded SSE events for a team run (for UI replay)."""
    store = _get_store()
    spec = await store.get(name)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Team '{name}' not found")
    require_access(_principal(), spec.owner_id, spec.shared_with)
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
    require_access(_principal(), spec.owner_id, spec.shared_with)
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
