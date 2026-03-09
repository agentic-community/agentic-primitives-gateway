from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from agentic_primitives_gateway._build_info import BUILD_REF
from agentic_primitives_gateway.config import Settings, settings
from agentic_primitives_gateway.context import get_request_id
from agentic_primitives_gateway.enforcement.middleware import PolicyEnforcementMiddleware
from agentic_primitives_gateway.middleware import RequestContextMiddleware
from agentic_primitives_gateway.registry import _load_class, registry
from agentic_primitives_gateway.routes import (
    agents,
    browser,
    code_interpreter,
    evaluations,
    gateway,
    health,
    identity,
    memory,
    observability,
    policy,
    teams,
    tools,
)
from agentic_primitives_gateway.watcher import ConfigWatcher

_old_record_factory = logging.getLogRecordFactory()


def _request_id_record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
    record = _old_record_factory(*args, **kwargs)
    record.request_id = get_request_id() or "-"  # type: ignore[attr-defined]
    return record


logging.setLogRecordFactory(_request_id_record_factory)
logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def _seed_policies() -> None:
    """Seed Cedar policies from config into the policy provider.

    Creates a 'seed' engine and populates it with policies defined in
    ``enforcement.seed_policies``.  Runs once at startup so that the
    noop (in-memory) provider always has a baseline policy set.
    """
    seed = settings.enforcement.seed_policies
    if not seed:
        return

    policy_provider = registry.policy
    engine = await policy_provider.create_policy_engine(
        name="seed",
        description="Auto-seeded from config",
    )
    engine_id = engine["policy_engine_id"]

    for sp in seed:
        await policy_provider.create_policy(
            engine_id=engine_id,
            policy_body=sp.policy_body,
            description=sp.description,
        )

    logger.info("Seeded %d policies into engine %s", len(seed), engine_id)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("agentic-primitives-gateway build=%s", BUILD_REF)
    registry.initialize()

    # Initialize agent store
    from agentic_primitives_gateway.agents.store import FileAgentStore
    from agentic_primitives_gateway.routes.agents import set_agent_store

    agent_store = FileAgentStore(path=settings.agents.store_path)
    if settings.agents.specs:
        agent_store.seed(settings.agents.specs)
    set_agent_store(agent_store)

    # Initialize team store
    from agentic_primitives_gateway.agents.team_store import FileTeamStore
    from agentic_primitives_gateway.routes.agents import _runner as agent_runner
    from agentic_primitives_gateway.routes.teams import get_team_runner, set_team_store

    team_store = FileTeamStore(path=settings.teams.store_path)
    if settings.teams.specs:
        team_store.seed(settings.teams.specs)
    set_team_store(team_store)
    get_team_runner().set_stores(agent_store, team_store, agent_runner)

    # Seed policies from config into the policy provider
    await _seed_policies()

    # Initialize policy enforcer
    from agentic_primitives_gateway.enforcement.base import PolicyEnforcer

    enforcer_cfg = settings.enforcement
    enforcer_cls = _load_class(enforcer_cfg.backend)
    enforcer: PolicyEnforcer = enforcer_cls(**enforcer_cfg.config)
    await enforcer.load_policies()
    if hasattr(enforcer, "start_refresh"):
        enforcer.start_refresh()
    app.state.enforcer = enforcer

    watcher: ConfigWatcher | None = None
    config_path = Settings.config_file_path()
    if config_path:
        watcher = ConfigWatcher(config_path, registry)
        await watcher.start()

    yield

    if watcher is not None:
        await watcher.stop()

    await enforcer.close()


app = FastAPI(
    title="Agentic Primitives Gateway",
    description="Unified API for agent infrastructure primitives",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
app.add_middleware(PolicyEnforcementMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> Response:
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ConnectionError)
async def connection_error_handler(request: Request, exc: ConnectionError) -> Response:
    from fastapi.responses import JSONResponse

    logger.warning("Connection error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": f"Service unavailable: {exc}"},
    )


@app.exception_handler(TimeoutError)
async def timeout_error_handler(request: Request, exc: TimeoutError) -> Response:
    from fastapi.responses import JSONResponse

    logger.warning("Timeout on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=504,
        content={"detail": f"Gateway timeout: {exc}"},
    )


@app.exception_handler(Exception)
async def general_error_handler(request: Request, exc: Exception) -> Response:
    from fastapi.responses import JSONResponse

    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )


@app.get("/metrics", tags=["metrics"], include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(health.router)
app.include_router(memory.router)
app.include_router(observability.router)
app.include_router(gateway.router)
app.include_router(tools.router)
app.include_router(identity.router)
app.include_router(code_interpreter.router)
app.include_router(browser.router)
app.include_router(policy.router)
app.include_router(evaluations.router)
app.include_router(agents.router)
app.include_router(teams.router)


# ── Provider discovery ──────────────────────────────────────────────


@app.get("/api/v1/providers", tags=["providers"])
async def list_providers() -> dict:
    """List available providers for each primitive."""
    return registry.list_providers()


@app.get("/api/v1/openapi", include_in_schema=False)
async def get_openapi_spec() -> dict:
    """Proxy for /openapi.json — accessible via /api/ prefix for dev proxy compatibility."""
    return app.openapi()


# ── Web UI (served from production build) ───────────────────────────

_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.exists():
    app.mount("/ui/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="ui-assets")

    @app.get("/ui", include_in_schema=False)
    async def ui_root() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/ui/{full_path:path}", include_in_schema=False)
    async def ui_spa(full_path: str) -> FileResponse:
        file_path = _STATIC_DIR / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(_STATIC_DIR / "index.html")
