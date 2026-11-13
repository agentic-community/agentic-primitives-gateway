import contextlib
import logging
from collections.abc import AsyncGenerator
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from starlette.responses import Response, StreamingResponse

from agentic_primitives_gateway import metrics
from agentic_primitives_gateway.agents.team_runner import TeamRunner
from agentic_primitives_gateway.agents.team_store import TeamStore
from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.auth.access import require_owner_or_admin, validate_team_pool_reference
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.models.agents import ForkRequest, RejectionRequest
from agentic_primitives_gateway.models.teams import (
    CreateTeamRequest,
    CreateTeamVersionRequest,
    TeamLineage,
    TeamListResponse,
    TeamRunRequest,
    TeamRunResponse,
    TeamSpec,
    TeamVersion,
    TeamVersionListResponse,
    UpdateTeamRequest,
)
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.routes._background import (
    BackgroundRunManager,
    reconnect_event_generator,
    run_event_key,
    sse_response,
)
from agentic_primitives_gateway.routes._helpers import (
    require_admin,
    require_principal,
    resolve_team_spec,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/teams", tags=["teams"])


async def _validate_team_pool(spec: TeamSpec, principal: AuthenticatedPrincipal, store: TeamStore) -> None:
    if not spec.shared_memory_namespace:
        return
    from agentic_primitives_gateway.routes.agents import _get_store as _get_agent_store

    await validate_team_pool_reference(spec, principal, _get_agent_store(), store)


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


def _team_run_key(spec: TeamSpec, run_id: str) -> str:
    return run_event_key("team", require_principal().id, spec.owner_id, spec.name, run_id)


def _team_run_index(spec: TeamSpec) -> str:
    return run_event_key("team-index", require_principal().id, spec.owner_id, spec.name, "runs")


def _ns_kind(owner_id: str) -> str:
    """Collapse ``owner_id`` → bounded metric label (``system`` or ``user``)."""
    return "system" if owner_id == "system" else "user"


@router.post("", response_model=TeamSpec, status_code=201)
async def create_team(request: CreateTeamRequest) -> TeamSpec:
    """Create a new team identity in the caller's namespace.

    See :func:`routes.agents.create_agent` for the versioning semantics.
    """
    store = _get_store()
    principal = require_principal()
    if principal.id == "system" and not principal.is_admin:
        raise HTTPException(status_code=403, detail="System namespace requires admin access")
    data = request.model_dump()
    data["owner_id"] = principal.id
    spec = TeamSpec(**data)
    await _validate_team_pool(spec, principal, store)

    existing = await store.get_deployed(spec.name, principal.id)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Team '{spec.name}' already exists")
    version = await store.create_version(
        name=spec.name,
        owner_id=principal.id,
        spec=spec,
        created_by=principal.id,
        commit_message="initial version",
        auto_deploy=True,
    )
    emit_audit_event(
        action=AuditAction.TEAM_CREATE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.TEAM,
        resource_id=f"{principal.id}:{spec.name}",
        metadata={
            "workers": list(spec.workers),
            "planner": spec.planner,
            "synthesizer": spec.synthesizer,
            "version_id": version.version_id,
        },
    )
    metrics.TEAM_VERSIONS_CREATED.labels(
        ns_kind=_ns_kind(principal.id),
        auto_deployed=str(version.status.value == "deployed").lower(),
    ).inc()
    return version.spec


@router.get("", response_model=TeamListResponse)
async def list_teams() -> TeamListResponse:
    store = _get_store()
    principal = require_principal()
    teams = await store.list_for_user(principal)
    return TeamListResponse(teams=teams)


@router.get("/{name}/export")
async def export_team(name: str, owner: str | None = None) -> Response:
    """Export a team spec as a standalone Python script."""
    from agentic_primitives_gateway.agents.export import export_team as _export
    from agentic_primitives_gateway.routes.agents import _get_store as _get_agent_store

    store = _get_store()
    principal = require_principal()
    spec: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)

    # Load all referenced agent specs.  Workers/planner/synthesizer resolve
    # in the team owner's namespace first, then fall back to ``system``.
    agent_store = _get_agent_store()
    agent_refs = {spec.planner, spec.synthesizer, *spec.workers}
    agent_specs: dict[str, Any] = {}
    from agentic_primitives_gateway.auth.access import check_access

    for ref in agent_refs:
        if ":" in ref:
            owner_id, _, bare = ref.partition(":")
            aspec = await agent_store.resolve_qualified(owner_id, bare)
        else:
            aspec = await agent_store.resolve_qualified(spec.owner_id, ref)
            if aspec is None:
                aspec = await agent_store.resolve_qualified("system", ref)
        if aspec and check_access(principal, aspec.owner_id, aspec.shared_with):
            agent_specs[ref] = aspec

    code = _export(spec, agent_specs)
    return Response(
        content=code,
        media_type="text/x-python",
        headers={"Content-Disposition": f'attachment; filename="{name}.py"'},
    )


@router.get("/{name}", response_model=TeamSpec)
async def get_team(name: str, owner: str | None = None) -> TeamSpec:
    store = _get_store()
    spec: TeamSpec = await resolve_team_spec(store, name, require_principal(), owner_query=owner)
    return spec


@router.put("/{name}", response_model=TeamSpec)
async def update_team(name: str, request: UpdateTeamRequest, owner: str | None = None) -> TeamSpec:
    """Create + auto-deploy a new version of a team.  Returns 409 under the
    admin-approval gate — see :func:`routes.agents.update_agent`.
    """
    from agentic_primitives_gateway.config import settings

    store = _get_store()
    principal = require_principal()
    existing: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)
    require_owner_or_admin(principal, existing.owner_id)

    if settings.governance.require_admin_approval_for_deploy:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Direct PUT is disabled while the admin-approval gate is on. Create a version via POST /versions.",
                "versions_url": f"/api/v1/teams/{existing.owner_id}:{existing.name}/versions",
            },
        )

    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    merged = existing.model_dump() | updates
    merged["owner_id"] = existing.owner_id
    merged["name"] = existing.name
    new_spec = TeamSpec(**merged)
    await _validate_team_pool(new_spec, principal, store)
    current_deployed = await store.get_deployed(existing.name, existing.owner_id)
    version = await store.create_version(
        name=existing.name,
        owner_id=existing.owner_id,
        spec=new_spec,
        created_by=principal.id,
        parent_version_id=current_deployed.version_id if current_deployed else None,
        commit_message="updated via PUT /teams/{name}",
        auto_deploy=True,
    )
    emit_audit_event(
        action=AuditAction.TEAM_UPDATE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.TEAM,
        resource_id=f"{existing.owner_id}:{existing.name}",
        metadata={"fields": sorted(updates.keys()), "version_id": version.version_id},
    )
    return version.spec


@router.delete("/{name}")
async def delete_team(name: str, owner: str | None = None) -> dict[str, Any]:
    store = _get_store()
    principal = require_principal()
    spec: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)
    require_owner_or_admin(principal, spec.owner_id)
    archived = await store.archive_identity(spec.name, spec.owner_id)
    emit_audit_event(
        action=AuditAction.TEAM_DELETE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.TEAM,
        resource_id=f"{spec.owner_id}:{spec.name}",
        metadata={"versions_archived": archived},
    )
    return {"status": "deleted", "versions_archived": archived}


@router.post("/{name}/run", response_model=TeamRunResponse)
async def run_team(name: str, request: TeamRunRequest) -> TeamRunResponse:
    store = _get_store()
    spec = await resolve_team_spec(store, name, require_principal())
    return await _runner.run(team_spec=spec, message=request.message)


@router.post("/{name}/run/stream")
async def run_team_stream(name: str, request: TeamRunRequest) -> StreamingResponse:
    """Streaming variant of team run. Returns SSE events.

    The team run executes in a background task so that it completes even
    if the client disconnects mid-stream.
    """
    store = _get_store()
    spec = await resolve_team_spec(store, name, require_principal())
    principal = require_principal()

    # Obtain the generated ID before spawning the producer: no event or status
    # can be persisted until its owner and scoped key have been claimed.
    stream = cast(AsyncGenerator[dict[str, Any]], _runner.run_stream(team_spec=spec, message=request.message))
    try:
        first_event = await anext(stream)
    except BaseException:
        await stream.aclose()
        raise
    run_id = first_event["team_run_id"]
    key = _team_run_key(spec, run_id)
    if not await _bg.reserve(key, principal.id, claim_key=f"team-id:{run_id}", claim_identity=key):
        await stream.aclose()
        raise HTTPException(status_code=409, detail="Team run ID already in use")

    async def events():
        try:
            yield first_event
            async for event in stream:
                yield event
        finally:
            await stream.aclose()

    try:
        queue, _ = _bg.start(
            key,
            events(),
            owner_id=principal.id,
            record_events=True,
            index_key=_team_run_index(spec),
            index_run_id=run_id,
        )
    except BaseException:
        await _bg.abandon(key)
        await stream.aclose()
        raise
    return sse_response(queue)


@router.get("/{name}/runs")
async def list_team_runs(name: str) -> dict:
    """List all known runs for a team.

    Checks both in-memory active runs and a per-team Redis index for
    runs associated with this team.
    """
    store = _get_store()
    spec = await resolve_team_spec(store, name, require_principal())

    runs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # Both local entries and the Redis index belong to this caller and team.
    for key, (task, _, event_log, _started) in _bg.runs.items():
        if not event_log or event_log[0].get("type") != "team_start":
            continue
        run_id = event_log[0].get("team_run_id")
        if not run_id or key != _team_run_key(spec, run_id):
            continue
        if await _bg.get_owner_async(key) != require_principal().id:
            continue
        runs.append({"team_run_id": run_id, "status": "running" if not task.done() else "idle"})
        seen_ids.add(run_id)

    if _bg._event_store:
        try:
            indexed_ids = await _bg._event_store.get_index(_team_run_index(spec))
            for run_id in indexed_ids:
                if run_id in seen_ids:
                    continue
                key = _team_run_key(spec, run_id)
                if await _bg.get_owner_async(key) != require_principal().id:
                    continue
                status = await _bg.get_status_async(key)
                runs.append({"team_run_id": run_id, "status": status})
        except Exception:
            logger.debug("Failed to read team run index from Redis", exc_info=True)

    return {"team_name": name, "runs": runs}


async def _require_run_owner(spec: TeamSpec, team_run_id: str) -> str:
    """Return the scoped key only for a known run belonging to this team."""
    key = _team_run_key(spec, team_run_id)
    principal = require_principal()
    owner = await _bg.get_owner_async(key)
    if owner is None or (owner != principal.id and not principal.is_admin):
        raise HTTPException(status_code=403, detail="Forbidden")
    return key


@router.delete("/{name}/runs/{team_run_id}")
async def delete_team_run(name: str, team_run_id: str) -> dict:
    """Delete a team run's data (tasks, events)."""
    store = _get_store()
    spec = await resolve_team_spec(store, name, require_principal())
    key = await _require_run_owner(spec, team_run_id)

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
            await _bg._event_store.delete(key)

    # Remove from local tracking
    _bg.runs.pop(key, None)

    return {"status": "deleted"}


@router.get("/{name}/runs/{team_run_id}/stream")
async def stream_team_run_events(name: str, team_run_id: str) -> StreamingResponse:
    """SSE stream of team run events from the event store.

    Replays all existing events, then polls for new events every second
    until the run completes. Used by the UI to reconnect after a stream
    drop (e.g. server restart with checkpoint recovery).
    """
    store = _get_store()
    spec = await resolve_team_spec(store, name, require_principal())
    key = await _require_run_owner(spec, team_run_id)

    return StreamingResponse(
        reconnect_event_generator(_bg, key, done_event_types=frozenset({"done", "cancelled"})),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{name}/runs/{team_run_id}/status")
async def get_team_run_status(name: str, team_run_id: str) -> dict:
    """Check if a team run is currently active."""
    store = _get_store()
    spec = await resolve_team_spec(store, name, require_principal())
    key = await _require_run_owner(spec, team_run_id)

    _bg.cleanup()
    return {"status": await _bg.get_status_async(key)}


@router.delete("/{name}/runs/{team_run_id}/cancel")
async def cancel_team_run(name: str, team_run_id: str) -> dict:
    """Cancel an active team run and mark all in-progress tasks as failed.

    Works for both locally-tracked runs (via BackgroundRunManager) and
    recovered runs (which bypass the manager). For recovered runs, does
    a "soft cancel" — marks tasks as failed and deletes the checkpoint
    so the run stops naturally.
    """
    store = _get_store()
    spec = await resolve_team_spec(store, name, require_principal())
    key = await _require_run_owner(spec, team_run_id)
    from agentic_primitives_gateway.routes.agents import _runner as agent_runner

    if agent_runner._checkpoint_store:
        await agent_runner._checkpoint_store.mark_cancelled(key)

    # Signal the team runner to stop workers at the next checkpoint
    _runner.cancel_run(team_run_id, event_key=key)

    # Try to cancel the local background task (works for non-recovered runs)
    await _bg.cancel(key)

    # Also try to cancel a recovery task (works for resumed runs on this replica)
    from agentic_primitives_gateway.agents.checkpoint import cancel_recovery_task

    cancel_recovery_task(key)

    # Always mark tasks as failed — works for both local and recovered runs
    try:
        all_tasks = await registry.tasks.list_tasks(team_run_id)
        for t in all_tasks:
            if t.status in ("pending", "claimed", "in_progress"):
                await registry.tasks.update_task(team_run_id, t.id, status="failed", result="Run cancelled")
    except (NotImplementedError, Exception):
        pass

    # Set event store status to cancelled
    if _bg._event_store:
        with contextlib.suppress(Exception):
            await _bg._event_store.set_status(key, "cancelled")
            await _bg._event_store.append_event(key, {"type": "cancelled"})

    # Delete checkpoint so orphan recovery doesn't resume this run
    if agent_runner._checkpoint_store:
        with contextlib.suppress(Exception):
            await agent_runner._checkpoint_store.delete(key)

    return {"status": "cancelled"}


@router.get("/{name}/runs/{team_run_id}/events")
async def get_team_run_events(name: str, team_run_id: str) -> dict:
    """Return all recorded SSE events for a team run (for UI replay)."""
    store = _get_store()
    spec = await resolve_team_spec(store, name, require_principal())
    key = await _require_run_owner(spec, team_run_id)

    _bg.cleanup()
    status = await _bg.get_status_async(key)
    events = await _bg.get_events_async(key)
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
    spec = await resolve_team_spec(store, name, require_principal())
    key = await _require_run_owner(spec, team_run_id)

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
    status = await _bg.get_status_async(key)

    return {
        "team_run_id": team_run_id,
        "team_name": name,
        "status": status,
        "tasks": tasks_out,
        "tasks_created": len(all_tasks),
        "tasks_completed": done_count,
    }


@router.post("/{name}/runs/{team_run_id}/tasks/{task_id}/retry")
async def retry_task(name: str, team_run_id: str, task_id: str) -> StreamingResponse:
    """Retry a single failed task within a team run.

    Resets the task to in_progress, recovers partial tokens from the
    event store as resume context, and re-executes the assigned worker
    agent. Returns an SSE stream of events.
    """
    store = _get_store()
    spec = await resolve_team_spec(store, name, require_principal())
    await _require_run_owner(spec, team_run_id)
    principal = require_principal()
    retry_key = run_event_key("team-retry", principal.id, spec.owner_id, spec.name, f"{team_run_id}:retry:{task_id}")
    if not await _bg.reserve(
        retry_key,
        principal.id,
        claim_key=f"team-retry-id:{team_run_id}:retry:{task_id}",
        claim_identity=retry_key,
        reuse=True,
    ):
        raise HTTPException(status_code=409, detail="Task retry already running")
    try:
        await _bg.reset_events(retry_key)
        queue, _ = _bg.start(
            retry_key,
            _runner.retry_task_stream(team_spec=spec, team_run_id=team_run_id, task_id=task_id),
            owner_id=principal.id,
            record_events=True,
        )
    except BaseException:
        await _bg.abandon(retry_key)
        raise
    return sse_response(queue)


# ── Versioning / fork / lineage ────────────────────────────────────────


@router.get("/{name}/versions", response_model=TeamVersionListResponse)
async def list_team_versions(name: str, owner: str | None = None) -> TeamVersionListResponse:
    """List all versions for a team identity (all statuses)."""
    store = _get_store()
    principal = require_principal()
    spec: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)
    versions = await store.list_versions(spec.name, spec.owner_id)
    return TeamVersionListResponse(versions=versions)


@router.get("/{name}/versions/{version_id}", response_model=TeamVersion)
async def get_team_version(name: str, version_id: str, owner: str | None = None) -> TeamVersion:
    store = _get_store()
    principal = require_principal()
    spec: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)
    v = await store.get_version(spec.name, spec.owner_id, version_id)
    if v is None:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found")
    return v


@router.post("/{name}/versions", response_model=TeamVersion, status_code=201)
async def create_team_version(name: str, request: CreateTeamVersionRequest, owner: str | None = None) -> TeamVersion:
    """Create a new version of a team.

    Auto-deploys when the admin-approval gate is off; otherwise lands as
    ``draft`` requiring ``propose`` → ``approve`` → ``deploy``.
    """
    store = _get_store()
    principal = require_principal()
    existing: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)
    require_owner_or_admin(principal, existing.owner_id)

    updates = {
        k: v for k, v in request.model_dump(exclude={"commit_message", "parent_version_id"}).items() if v is not None
    }
    merged = existing.model_dump() | updates
    merged["owner_id"] = existing.owner_id
    merged["name"] = existing.name
    new_spec = TeamSpec(**merged)
    await _validate_team_pool(new_spec, principal, store)
    parent_id = request.parent_version_id
    if parent_id is None:
        current_deployed = await store.get_deployed(existing.name, existing.owner_id)
        parent_id = current_deployed.version_id if current_deployed else None
    version = await store.create_version(
        name=existing.name,
        owner_id=existing.owner_id,
        spec=new_spec,
        created_by=principal.id,
        parent_version_id=parent_id,
        commit_message=request.commit_message,
        auto_deploy=True,
    )
    emit_audit_event(
        action=AuditAction.TEAM_VERSION_CREATE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.TEAM,
        resource_id=f"{existing.owner_id}:{existing.name}",
        metadata={
            "version_id": version.version_id,
            "version_number": version.version_number,
            "parent_version_id": parent_id,
            "commit_message": request.commit_message,
            "auto_deployed": version.status.value == "deployed",
        },
    )
    metrics.TEAM_VERSIONS_CREATED.labels(
        ns_kind=_ns_kind(existing.owner_id),
        auto_deployed=str(version.status.value == "deployed").lower(),
    ).inc()
    return version


@router.post("/{name}/versions/{version_id}/propose", response_model=TeamVersion)
async def propose_team_version(name: str, version_id: str, owner: str | None = None) -> TeamVersion:
    store = _get_store()
    principal = require_principal()
    spec: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)
    require_owner_or_admin(principal, spec.owner_id)
    try:
        version = await store.propose_version(spec.name, spec.owner_id, version_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    emit_audit_event(
        action=AuditAction.TEAM_VERSION_PROPOSE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.TEAM,
        resource_id=f"{spec.owner_id}:{spec.name}",
        metadata={"version_id": version.version_id, "version_number": version.version_number},
    )
    return version


@router.post("/{name}/versions/{version_id}/approve", response_model=TeamVersion)
async def approve_team_version(name: str, version_id: str, owner: str | None = None) -> TeamVersion:
    principal = require_admin()
    store = _get_store()
    spec: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)
    try:
        version = await store.approve_version(spec.name, spec.owner_id, version_id, approver_id=principal.id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    emit_audit_event(
        action=AuditAction.TEAM_VERSION_APPROVE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.TEAM,
        resource_id=f"{spec.owner_id}:{spec.name}",
        metadata={
            "version_id": version.version_id,
            "version_number": version.version_number,
            "approver_id": principal.id,
        },
    )
    metrics.TEAM_VERSION_APPROVALS.labels(outcome="approved").inc()
    return version


@router.post("/{name}/versions/{version_id}/reject", response_model=TeamVersion)
async def reject_team_version(
    name: str, version_id: str, request: RejectionRequest, owner: str | None = None
) -> TeamVersion:
    principal = require_admin()
    store = _get_store()
    spec: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)
    try:
        version = await store.reject_version(
            spec.name,
            spec.owner_id,
            version_id,
            approver_id=principal.id,
            reason=request.reason,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    emit_audit_event(
        action=AuditAction.TEAM_VERSION_REJECT,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.TEAM,
        resource_id=f"{spec.owner_id}:{spec.name}",
        metadata={
            "version_id": version.version_id,
            "version_number": version.version_number,
            "approver_id": principal.id,
            "reason_truncated": request.reason[:200],
        },
    )
    metrics.TEAM_VERSION_APPROVALS.labels(outcome="rejected").inc()
    return version


@router.post("/{name}/versions/{version_id}/deploy", response_model=TeamVersion)
async def deploy_team_version(name: str, version_id: str, owner: str | None = None) -> TeamVersion:
    store = _get_store()
    principal = require_principal()
    spec: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)
    require_owner_or_admin(principal, spec.owner_id)
    candidate = await store.get_version(spec.name, spec.owner_id, version_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found")
    await _validate_team_pool(candidate.spec, principal, store)
    previous = await store.get_deployed(spec.name, spec.owner_id)
    try:
        version = await store.deploy_version(spec.name, spec.owner_id, version_id, deployed_by=principal.id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Version '{version_id}' not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    emit_audit_event(
        action=AuditAction.TEAM_VERSION_DEPLOY,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.TEAM,
        resource_id=f"{spec.owner_id}:{spec.name}",
        metadata={
            "version_id": version.version_id,
            "version_number": version.version_number,
            "previous_version_id": previous.version_id if previous else None,
        },
    )
    metrics.TEAM_VERSION_APPROVALS.labels(outcome="deployed").inc()
    return version


@router.post("/{name}/fork", response_model=TeamVersion, status_code=201)
async def fork_team(name: str, request: ForkRequest, owner: str | None = None) -> TeamVersion:
    """Fork an accessible team into the caller's namespace.

    Worker/planner/synthesizer references in the forked spec are
    auto-qualified to the source owner's namespace so the forked team
    keeps working.
    """
    store = _get_store()
    principal = require_principal()
    if principal.id == "system" and not principal.is_admin:
        raise HTTPException(status_code=403, detail="System namespace requires admin access")
    source: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)
    target = source.model_copy(update={"owner_id": principal.id, "name": request.target_name or source.name})
    await _validate_team_pool(target, principal, store)
    try:
        version = await store.fork(
            source_name=source.name,
            source_owner_id=source.owner_id,
            target_owner_id=principal.id,
            target_name=request.target_name,
            created_by=principal.id,
            commit_message=request.commit_message,
        )
    except KeyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    emit_audit_event(
        action=AuditAction.TEAM_FORK,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.TEAM,
        resource_id=f"{principal.id}:{version.team_name}",
        metadata={
            "source_owner_id": source.owner_id,
            "source_name": source.name,
            "source_version_id": version.forked_from.version_id if version.forked_from else None,
            "target_owner_id": principal.id,
            "target_name": version.team_name,
            "target_version_id": version.version_id,
        },
    )
    metrics.TEAM_FORKS.labels(source_ns_kind=_ns_kind(source.owner_id)).inc()
    return version


@router.get("/{name}/lineage", response_model=TeamLineage)
async def get_team_lineage(name: str, owner: str | None = None) -> TeamLineage:
    store = _get_store()
    principal = require_principal()
    spec: TeamSpec = await resolve_team_spec(store, name, principal, owner_query=owner)
    return await store.get_lineage_model(spec.name, spec.owner_id)
