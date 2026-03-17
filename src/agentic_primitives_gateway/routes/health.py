import asyncio
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

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

    done, _ = await asyncio.wait({task}, timeout=_HEALTHCHECK_TIMEOUT)
    if done:
        try:
            result = task.result()
            # Providers can return bool (legacy) or str ("ok"/"reachable")
            status = result if isinstance(result, str) else "ok" if result else "down"
        except Exception:
            logger.debug("Healthcheck failed: %s", key, exc_info=True)
            status = "down"
    else:
        logger.debug("Healthcheck timed out: %s", key)
        status = "down"

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

    try:
        result = await asyncio.wait_for(provider.healthcheck(), timeout=_HEALTHCHECK_TIMEOUT)
        status = result if isinstance(result, str) else "ok" if result else "down"
    except TimeoutError:
        status = "down"
    except Exception:
        logger.debug("Authenticated healthcheck failed: %s", key, exc_info=True)
        status = "down"

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
