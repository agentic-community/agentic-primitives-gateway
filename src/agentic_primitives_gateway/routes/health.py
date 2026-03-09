import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentic_primitives_gateway.metrics import PROVIDER_HEALTH
from agentic_primitives_gateway.models.enums import HealthStatus
from agentic_primitives_gateway.registry import PRIMITIVES, registry
from agentic_primitives_gateway.watcher import get_last_reload_error

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

# Per-provider timeout for healthchecks (seconds)
_HEALTHCHECK_TIMEOUT = 2.0


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    return {"status": HealthStatus.OK}


async def _check_provider(primitive: str, provider_name: str) -> tuple[str, str, str, bool]:
    """Run a single provider healthcheck with a timeout.

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
            healthy = task.result()
        except Exception:
            logger.debug("Healthcheck failed: %s", key, exc_info=True)
            healthy = False
    else:
        logger.debug("Healthcheck timed out: %s", key)
        healthy = False

    return primitive, provider_name, key, healthy


@router.get("/readyz")
async def readiness() -> JSONResponse:
    checks: dict[str, bool] = {}
    try:
        tasks = []
        for primitive in PRIMITIVES:
            prim_providers = registry.get_primitive(primitive)
            for provider_name in prim_providers.names:
                tasks.append(_check_provider(primitive, provider_name))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.exception("Healthcheck task failed", exc_info=result)
                continue
            primitive, provider_name, key, healthy = result
            checks[key] = healthy
            PROVIDER_HEALTH.labels(
                primitive=primitive,
                provider=provider_name,
            ).set(1 if healthy else 0)
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse(
            status_code=503,
            content={"status": HealthStatus.ERROR, "checks": checks},
        )

    all_healthy = all(checks.values())

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

    return JSONResponse(
        status_code=200 if all_healthy else 503,
        content={"status": HealthStatus.OK if all_healthy else HealthStatus.DEGRADED, "checks": checks},
    )
