from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from agentic_primitives_gateway._build_info import BUILD_REF
from agentic_primitives_gateway.config import Settings, settings
from agentic_primitives_gateway.context import (
    AWSCredentials,
    get_request_id,
    set_aws_credentials,
    set_provider_overrides,
    set_request_id,
    set_service_credentials,
)
from agentic_primitives_gateway.registry import PRIMITIVES, registry
from agentic_primitives_gateway.routes import (
    agents,
    browser,
    code_interpreter,
    gateway,
    health,
    identity,
    memory,
    observability,
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


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Extract AWS credentials and provider routing from request headers.

    AWS credential headers:
        X-AWS-Access-Key-Id       (required for pass-through)
        X-AWS-Secret-Access-Key   (required for pass-through)
        X-AWS-Session-Token       (optional, for temporary credentials)
        X-AWS-Region              (optional, overrides provider default)

    Service credential headers (generic, for any service):
        X-Cred-{Service}-{Key}    e.g. X-Cred-Langfuse-Public-Key
        Parsed into: {"langfuse": {"public_key": "..."}}

    Provider routing headers:
        X-Provider                (default provider for all primitives)
        X-Provider-Memory         (override for memory)
        X-Provider-Identity       (override for identity)
        X-Provider-Code-Interpreter (override for code_interpreter)
        X-Provider-Browser        (override for browser)
        X-Provider-Observability  (override for observability)
        X-Provider-Gateway        (override for gateway)
        X-Provider-Tools          (override for tools)
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Request ID
        request_id = request.headers.get("x-request-id") or uuid4().hex
        set_request_id(request_id)

        # AWS credentials
        access_key = request.headers.get("x-aws-access-key-id")
        secret_key = request.headers.get("x-aws-secret-access-key")

        if access_key and secret_key:
            set_aws_credentials(
                AWSCredentials(
                    access_key_id=access_key,
                    secret_access_key=secret_key,
                    session_token=request.headers.get("x-aws-session-token"),
                    region=request.headers.get("x-aws-region"),
                )
            )
        else:
            set_aws_credentials(None)

        # Service credentials (X-Cred-{Service}-{Key} headers)
        service_creds: dict[str, dict[str, str]] = {}
        for header_name, header_value in request.headers.items():
            if header_name.startswith("x-cred-"):
                parts = header_name.removeprefix("x-cred-").split("-", 1)
                if len(parts) == 2:
                    service = parts[0]
                    key = parts[1].replace("-", "_")
                    service_creds.setdefault(service, {})[key] = header_value
        set_service_credentials(service_creds)

        # Provider routing
        overrides: dict[str, str] = {}
        if default_provider := request.headers.get("x-provider"):
            overrides["default"] = default_provider
        for primitive in PRIMITIVES:
            header = f"x-provider-{primitive.replace('_', '-')}"
            if value := request.headers.get(header):
                overrides[primitive] = value
        set_provider_overrides(overrides)

        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response


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

    watcher: ConfigWatcher | None = None
    config_path = Settings.config_file_path()
    if config_path:
        watcher = ConfigWatcher(config_path, registry)
        await watcher.start()

    yield

    if watcher is not None:
        await watcher.stop()


app = FastAPI(
    title="Agentic Primitives Gateway",
    description="Unified API for agent infrastructure primitives",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(RequestContextMiddleware)
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
app.include_router(agents.router)


# ── Provider discovery ──────────────────────────────────────────────


@app.get("/api/v1/providers", tags=["providers"])
async def list_providers() -> dict:
    """List available providers for each primitive."""
    return registry.list_providers()
