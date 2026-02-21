from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from agentic_primitives_gateway.metrics import PROVIDER_HEALTH
from agentic_primitives_gateway.models.enums import HealthStatus
from agentic_primitives_gateway.registry import PRIMITIVES, registry

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    return {"status": HealthStatus.OK}


@router.get("/readyz")
async def readiness() -> JSONResponse:
    checks: dict[str, bool] = {}
    try:
        for primitive in PRIMITIVES:
            prim_providers = registry.get_primitive(primitive)
            for provider_name in prim_providers.names:
                provider = prim_providers.get(provider_name)
                key = f"{primitive}/{provider_name}"
                healthy = await provider.healthcheck()
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
    return JSONResponse(
        status_code=200 if all_healthy else 503,
        content={"status": HealthStatus.OK if all_healthy else HealthStatus.DEGRADED, "checks": checks},
    )
