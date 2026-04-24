import asyncio
import contextvars
import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import PRIMITIVE_RESOURCE_TYPE, AuditAction, AuditOutcome
from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.metrics import PROVIDER_HEALTH
from agentic_primitives_gateway.models.enums import HealthStatus
from agentic_primitives_gateway.registry import PRIMITIVES, registry
from agentic_primitives_gateway.routes._helpers import require_principal
from agentic_primitives_gateway.watcher import get_last_reload_error

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

# Per-provider timeout for healthchecks (seconds).
# Each check runs in a new thread + event loop, so this must account for
# both the actual I/O and the asyncio.run() overhead.
_HEALTHCHECK_TIMEOUT = 5.0


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    return {"status": HealthStatus.OK}


@router.get("/auth/config")
async def auth_config() -> dict:
    """Return auth configuration for the UI to initiate OIDC login.

    Exposes only the public-facing fields needed by the frontend:
    backend type, issuer, client_id, and scopes. Never exposes secrets.
    """
    auth = settings.auth
    if auth.backend == "jwt":
        jwt_cfg = auth.jwt
        # client_id for the UI OIDC flow. Falls back to audience if not set.
        client_id = jwt_cfg.get("client_id") or jwt_cfg.get("audience") or ""
        return {
            "backend": "jwt",
            "issuer": jwt_cfg.get("issuer", ""),
            "client_id": client_id,
            "scopes": "openid profile email",
        }
    return {"backend": auth.backend}


@router.get("/api/v1/auth/whoami")
async def whoami() -> dict:
    """Return the authenticated principal for the UI.

    Used by the web UI to render admin-only navigation and gate the audit
    viewer route.  Goes through ``AuthenticationMiddleware`` — noop auth
    returns the noop admin principal; JWT / api_key backends return 401
    when credentials are missing or invalid.
    """
    principal = require_principal()
    return {
        "id": principal.id,
        "type": principal.type,
        "is_admin": principal.is_admin,
        "groups": sorted(principal.groups),
        "scopes": sorted(principal.scopes),
    }


def _emit_healthcheck_event(
    primitive: str,
    provider_name: str,
    status: str,
    *,
    exc: BaseException | None = None,
) -> None:
    """Emit a ``provider.healthcheck`` audit event.

    ``status`` ∈ ``{"ok", "reachable", "down", "timeout"}``.  When ``exc``
    is set (i.e. the provider raised), ``error_type`` and a truncated
    ``error_message`` land in metadata so operators can see *why* the
    backend is unreachable without digging through server logs.
    """
    healthy = status in {"ok", "reachable"}
    metadata: dict[str, Any] = {
        "primitive": primitive,
        "provider": provider_name,
        "status": status,
    }
    if exc is not None:
        metadata["error_type"] = type(exc).__name__
        metadata["error_message"] = str(exc)[:512]
    emit_audit_event(
        action=AuditAction.PROVIDER_HEALTHCHECK,
        outcome=AuditOutcome.SUCCESS if healthy else AuditOutcome.FAILURE,
        resource_type=PRIMITIVE_RESOURCE_TYPE.get(primitive),
        resource_id=f"{primitive}/{provider_name}",
        metadata=metadata,
    )


async def _check_provider(primitive: str, provider_name: str) -> tuple[str, str, str, str]:
    """Run a single provider healthcheck with a timeout.

    Returns a status string: ``"ok"`` (fully healthy), ``"reachable"``
    (server up but needs user credentials), ``"down"``, or ``"timeout"``.

    Each healthcheck runs in its own thread via ``asyncio.run`` for two
    independent reasons:

    1. **Isolation from blocking providers.** Some providers (e.g. mem0
       constructing a Milvus client) block synchronously inside their
       async healthcheck. On the main event loop that would stall every
       other concurrent healthcheck past the timeout budget. The thread
       pool isolates each check so one slow provider doesn't starve the
       others.
    2. **Cooperation with ``asyncio.wait``.** We use ``asyncio.wait`` with
       a timeout (not ``asyncio.wait_for``) because ``wait_for`` tries to
       cancel the thread-bound future, which blocks until the thread
       finishes anyway.

    Contextvar propagation: we snapshot the request's context via
    ``copy_context()`` and invoke the thread's work inside ``ctx.run``.
    That carries the authenticated principal, AWS credentials, resolved
    service credentials, and correlation id into the worker — so both
    ``emit_audit_event`` (reading the principal) and the provider's
    healthcheck (reading per-request creds) behave identically inside
    the thread and on the main loop. For exempt callers (``/readyz``,
    kubelet), the snapshot has the anonymous principal — same visible
    outcome as before.
    """
    provider = registry.get_primitive(primitive).get(provider_name)
    key = f"{primitive}/{provider_name}"

    # Capture request-scoped context (principal, creds, correlation_id)
    # and carry it into the worker thread.
    ctx = contextvars.copy_context()

    def _run_in_thread() -> Any:
        return ctx.run(asyncio.run, provider.healthcheck())

    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(loop.run_in_executor(None, _run_in_thread))

    exc: BaseException | None = None
    done, _ = await asyncio.wait({task}, timeout=_HEALTHCHECK_TIMEOUT)
    if done:
        try:
            result = task.result()
            # Providers can return bool or str ("ok"/"reachable")
            status = result if isinstance(result, str) else "ok" if result else "down"
        except Exception as e:
            logger.debug("Healthcheck failed: %s", key, exc_info=True)
            status = "down"
            exc = e
    else:
        logger.debug("Healthcheck timed out: %s", key)
        status = "timeout"

    # Emit on the main loop inside the request's own context (not the
    # snapshot we passed to the thread), so the event reaches whichever
    # router is installed on this replica.
    _emit_healthcheck_event(primitive, provider_name, status, exc=exc)
    return primitive, provider_name, key, status


async def _run_all_healthchecks() -> dict[str, str]:
    """Run every configured provider's healthcheck in parallel.

    Single source of truth for both ``/readyz`` and
    ``/api/v1/providers/status``. The caller's identity (anonymous for
    exempt routes, authenticated for the dashboard) is automatically
    picked up from contextvars by ``_check_provider``, so both endpoints
    share one implementation.
    """
    tasks = [
        _check_provider(primitive, provider_name)
        for primitive in PRIMITIVES
        for provider_name in registry.get_primitive(primitive).names
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    checks: dict[str, str] = {}
    for result in results:
        if isinstance(result, BaseException):
            logger.exception("Healthcheck task failed", exc_info=result)
            continue
        prim, prov, key, status = result
        checks[key] = status
        PROVIDER_HEALTH.labels(primitive=prim, provider=prov).set(1 if status != "down" else 0)
    return checks


@router.get("/readyz")
async def readiness() -> JSONResponse:
    """Kubernetes readiness probe.

    Auth-exempt so kubelet can call it without credentials. Runs every
    provider's healthcheck via the shared ``_run_all_healthchecks``
    helper; returns 503 when any provider is ``down`` or the last
    config reload failed.
    """
    try:
        checks = await _run_all_healthchecks()
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse(
            status_code=503,
            content={"status": HealthStatus.ERROR, "checks": {}},
        )

    any_down = any(s == "down" for s in checks.values())

    reload_error = get_last_reload_error()
    if reload_error:
        return JSONResponse(
            status_code=503,
            content={
                "status": HealthStatus.DEGRADED,
                "checks": checks,
                "config_reload_error": reload_error,
            },
        )

    # "reachable" (needs user creds) is not a failure — only "down" degrades.
    return JSONResponse(
        status_code=200 if not any_down else 503,
        content={"status": HealthStatus.OK if not any_down else HealthStatus.DEGRADED, "checks": checks},
    )


@router.get(
    "/api/v1/providers/status",
    dependencies=[Depends(require_principal)],
    tags=["providers"],
)
async def provider_status() -> dict:
    """Per-user provider healthcheck.

    Runs behind auth + credential resolution middleware so each provider's
    ``healthcheck()`` sees the authenticated user's resolved credentials.
    Shares implementation with ``/readyz`` — the only difference is
    whichever principal is in contextvars when the checks run. Dashboard
    callers get user-attributed ``provider.healthcheck`` audit events;
    probe callers get anonymous ones.
    """
    return {"checks": await _run_all_healthchecks()}
