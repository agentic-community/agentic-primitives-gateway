import asyncio
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
    (server up but needs user credentials), or ``"down"``.

    Each healthcheck runs in its own thread via ``asyncio.run`` so that
    providers which block the event loop (e.g. synchronous gRPC connects
    in mem0) don't stall other concurrent checks.  We use ``asyncio.wait``
    instead of ``asyncio.wait_for`` because the latter tries to cancel then
    await executor futures, which blocks until the thread finishes.
    """
    provider = registry.get_primitive(primitive).get(provider_name)
    key = f"{primitive}/{provider_name}"

    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(loop.run_in_executor(None, lambda: asyncio.run(provider.healthcheck())))

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

    _emit_healthcheck_event(primitive, provider_name, status, exc=exc)
    return primitive, provider_name, key, status


@router.get("/readyz")
async def readiness() -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        tasks = []
        for primitive in PRIMITIVES:
            prim_providers = registry.get_primitive(primitive)
            for provider_name in prim_providers.names:
                tasks.append(_check_provider(primitive, provider_name))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, BaseException):
                logger.exception("Healthcheck task failed", exc_info=result)
                continue
            prim, prov, key, status = result
            checks[key] = status
            PROVIDER_HEALTH.labels(
                primitive=prim,
                provider=prov,
            ).set(1 if status != "down" else 0)
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse(
            status_code=503,
            content={"status": HealthStatus.ERROR, "checks": checks},
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


# ── Authenticated provider status (runs with user credentials) ──────


async def _check_provider_authenticated(primitive: str, provider_name: str) -> tuple[str, str, str, str]:
    """Healthcheck that runs on the main event loop with user credentials in context.

    Unlike ``_check_provider`` (which runs in a thread pool with a fresh
    event loop), this runs directly so that providers see the request-scoped
    credentials populated by ``CredentialResolutionMiddleware``.
    """

    provider = registry.get_primitive(primitive).get(provider_name)
    key = f"{primitive}/{provider_name}"

    exc: BaseException | None = None
    try:
        result = await asyncio.wait_for(provider.healthcheck(), timeout=_HEALTHCHECK_TIMEOUT)
        status = result if isinstance(result, str) else "ok" if result else "down"
    except TimeoutError:
        status = "timeout"
    except Exception as e:
        logger.debug("Authenticated healthcheck failed: %s", key, exc_info=True)
        status = "down"
        exc = e

    _emit_healthcheck_event(primitive, provider_name, status, exc=exc)
    return primitive, provider_name, key, status


@router.get(
    "/api/v1/providers/status",
    dependencies=[Depends(require_principal)],
    tags=["providers"],
)
async def provider_status() -> dict:
    """Per-user provider healthcheck.

    Runs behind auth + credential resolution middleware so each provider's
    ``healthcheck()`` sees the authenticated user's resolved credentials.
    Providers that returned ``"reachable"`` on ``/readyz`` (no server creds)
    will attempt authenticated checks here and may return ``"ok"`` if the
    user has valid credentials stored.
    """
    checks: dict[str, str] = {}
    tasks = []
    for primitive in PRIMITIVES:
        prim_providers = registry.get_primitive(primitive)
        for provider_name in prim_providers.names:
            tasks.append(_check_provider_authenticated(primitive, provider_name))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            continue
        _prim, _prov, key, status = result
        checks[key] = status

    return {"checks": checks}
