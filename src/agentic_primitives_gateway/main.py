from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

# Suppress noisy gRPC/abseil fork warnings from healthcheck thread pool.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "0")
# Disable Langfuse SDK telemetry (PostHog pings to us.i.posthog.com).
os.environ.setdefault("LANGFUSE_SDK_TELEMETRY_ENABLED", "false")

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from agentic_primitives_gateway._build_info import BUILD_REF
from agentic_primitives_gateway.audit.base import AuditSink
from agentic_primitives_gateway.audit.emit import configure_redaction, set_audit_router
from agentic_primitives_gateway.audit.log_formatter import JsonLogFormatter, LogSanitizationFilter
from agentic_primitives_gateway.audit.middleware import AuditMiddleware
from agentic_primitives_gateway.audit.router import AuditRouter
from agentic_primitives_gateway.audit.sinks.stdout_json import StdoutJsonSink
from agentic_primitives_gateway.auth.base import AuthBackend
from agentic_primitives_gateway.auth.middleware import AuthenticationMiddleware
from agentic_primitives_gateway.config import (
    AGENT_STORE_ALIASES,
    AUDIT_SINK_ALIASES,
    AUTH_BACKEND_ALIASES,
    CREDENTIAL_RESOLVER_ALIASES,
    CREDENTIAL_WRITER_ALIASES,
    TEAM_STORE_ALIASES,
    Settings,
    settings,
)
from agentic_primitives_gateway.context import (
    get_authenticated_principal,
    get_correlation_id,
    get_request_id,
)
from agentic_primitives_gateway.credentials.base import CredentialResolver
from agentic_primitives_gateway.credentials.middleware import CredentialResolutionMiddleware
from agentic_primitives_gateway.enforcement.base import PolicyEnforcer
from agentic_primitives_gateway.enforcement.middleware import PolicyEnforcementMiddleware
from agentic_primitives_gateway.middleware import RequestContextMiddleware
from agentic_primitives_gateway.registry import _load_class, registry
from agentic_primitives_gateway.routes import (
    a2a,
    admin_proposals,
    agents,
    browser,
    code_interpreter,
    credentials,
    evaluations,
    health,
    identity,
    llm,
    memory,
    observability,
    policy,
    teams,
    tools,
)
from agentic_primitives_gateway.routes import (
    audit as audit_routes,
)
from agentic_primitives_gateway.watcher import ConfigWatcher

_old_record_factory = logging.getLogRecordFactory()


def _request_id_record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
    record = _old_record_factory(*args, **kwargs)
    record.request_id = get_request_id() or "-"  # type: ignore[attr-defined]
    record.correlation_id = get_correlation_id() or "-"  # type: ignore[attr-defined]
    principal = get_authenticated_principal()
    record.principal_id = principal.id if principal else "-"  # type: ignore[attr-defined]
    record.principal_type = principal.type if principal else "-"  # type: ignore[attr-defined]
    return record


logging.setLogRecordFactory(_request_id_record_factory)

_log_handler = logging.StreamHandler()
if settings.logging.format == "json":
    _log_handler.setFormatter(JsonLogFormatter())
else:
    _log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"))
if settings.logging.sanitize:
    _log_handler.addFilter(LogSanitizationFilter())

_root_logger = logging.getLogger()
_root_logger.setLevel(settings.log_level.upper())
# Replace any default handlers installed by prior basicConfig calls so the
# formatter + sanitization filter are the sole source of output.
for _existing in list(_root_logger.handlers):
    _root_logger.removeHandler(_existing)
_root_logger.addHandler(_log_handler)

logger = logging.getLogger(__name__)


async def _seed_policies(engine_id: str) -> None:
    """Seed Cedar policies from config into the enforcer's policy engine.

    Populates the engine with policies defined in ``enforcement.seed_policies``.
    Runs once at startup so the gateway always has a baseline policy set.
    """
    seed = settings.enforcement.seed_policies
    if not seed:
        return

    policy_provider = registry.policy

    for sp in seed:
        await policy_provider.create_policy(
            engine_id=engine_id,
            policy_body=sp.policy_body,
            description=sp.description,
        )

    logger.info("Seeded %d policies into engine %s", len(seed), engine_id)


def _warn_replica_unsafe_config() -> None:
    """Log warnings if local-only backends are mixed with Redis-backed ones.

    This suggests a multi-replica deployment with an incomplete config — some
    components will work cross-replica but others won't.
    """
    agent_backend = settings.agents.store.backend
    team_backend = settings.teams.store.backend

    # Check if any store uses Redis (indicating multi-replica intent)
    has_redis = agent_backend == "redis" or team_backend == "redis"
    if not has_redis:
        return  # fully local — no warning needed

    warnings: list[str] = []
    if agent_backend != "redis":
        warnings.append(f"agents.store.backend={agent_backend!r} (should be 'redis')")
    if team_backend != "redis":
        warnings.append(f"teams.store.backend={team_backend!r} (should be 'redis')")

    # Check primitives for in-memory backends
    providers_cfg = settings.providers
    for prim_name in (
        "memory",
        "observability",
        "llm",
        "tools",
        "identity",
        "code_interpreter",
        "browser",
        "policy",
        "evaluations",
        "tasks",
    ):
        prim_cfg = getattr(providers_cfg, prim_name, None)
        if prim_cfg is None:
            continue
        for backend_name, backend_cfg in prim_cfg.backends.items():
            if "in_memory" in backend_cfg.backend.lower():
                warnings.append(
                    f"primitives.{prim_name}.{backend_name} uses in-memory provider ({backend_cfg.backend})"
                )

    if warnings:
        logger.warning(
            "Multi-replica config warning: Redis is enabled for some stores but "
            "local-only backends detected. These won't share state across replicas:\n  - %s",
            "\n  - ".join(warnings),
        )


def _build_audit_router() -> AuditRouter | None:
    """Construct the process-wide ``AuditRouter`` from ``settings.audit``.

    Returns ``None`` when audit is disabled in config.  Always includes a
    :class:`StdoutJsonSink` when ``stdout_json: true`` (the default) so
    operators get a usable audit stream out of the box.  Additional sinks
    are instantiated from ``settings.audit.sinks`` — each entry's ``name``
    must be unique.
    """
    audit_cfg = settings.audit
    if not audit_cfg.enabled:
        return None

    sinks: list[AuditSink] = []
    if audit_cfg.stdout_json:
        sinks.append(StdoutJsonSink(name="stdout_json"))

    for entry in audit_cfg.sinks:
        cls_path = AUDIT_SINK_ALIASES.get(entry.backend, entry.backend)
        sink_cls = _load_class(cls_path)
        sinks.append(sink_cls(name=entry.name, **entry.config))

    if not sinks:
        return None

    configure_redaction(
        extra_redact_keys=tuple(audit_cfg.redact_keys),
        redact_principal_id=audit_cfg.redact_principal_id,
    )
    return AuditRouter(
        sinks=sinks,
        queue_size=audit_cfg.queue_size,
        sink_timeout_seconds=audit_cfg.sink_timeout_seconds,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("agentic-primitives-gateway build=%s", BUILD_REF)
    registry.initialize()
    _warn_replica_unsafe_config()

    # Start the audit router before anything else so emits from the rest of
    # the startup path land on sinks.
    audit_router = _build_audit_router()
    if audit_router is not None:
        await audit_router.start()
        set_audit_router(audit_router)
        logger.info("Audit router started with %d sink(s)", len(audit_router.sinks))
    else:
        logger.info("Audit disabled")

    # Initialize stores via pluggable backend config
    from agentic_primitives_gateway.routes.agents import set_agent_store

    agent_store_cls = _load_class(AGENT_STORE_ALIASES.get(settings.agents.store.backend, settings.agents.store.backend))
    agent_store = agent_store_cls(**settings.agents.store.config)
    if settings.agents.specs:
        await agent_store.seed_async(settings.agents.specs)
    set_agent_store(agent_store)

    # Wire A2A dependencies (shares the same store and runner as agents)
    from agentic_primitives_gateway.routes.agents import _runner as agent_runner

    a2a.set_a2a_dependencies(agent_store, agent_runner)

    # Wire up background run manager + session registry from the store backend
    from agentic_primitives_gateway.routes.agents import set_agent_bg

    agent_bg = agent_store.create_background_run_manager(stale_seconds=600)
    if agent_bg:
        set_agent_bg(agent_bg)
        # Wire Redis into session ownership stores for multi-replica visibility
        if agent_bg._event_store and hasattr(agent_bg._event_store, "_redis"):
            from agentic_primitives_gateway.routes._helpers import (
                browser_session_owners,
                code_interpreter_session_owners,
            )

            browser_session_owners.set_redis(agent_bg._event_store._redis)
            code_interpreter_session_owners.set_redis(agent_bg._event_store._redis)
    agent_session_reg = agent_store.create_session_registry()
    if agent_session_reg:
        agent_runner.set_session_registry(agent_session_reg)

    # Wire checkpoint store + heartbeat for durable runs (Redis backends only)
    from agentic_primitives_gateway.agents.checkpoint import ReplicaHeartbeat, recover_orphaned_runs

    checkpoint_store = agent_store.create_checkpoint_store()
    heartbeat: ReplicaHeartbeat | None = None
    if checkpoint_store:
        heartbeat = ReplicaHeartbeat(checkpoint_store)
        await heartbeat.start()
        agent_runner.set_checkpoint_store(checkpoint_store, replica_id=heartbeat.replica_id)
        logger.info("Checkpoint store enabled (replica=%s)", heartbeat.replica_id)
    else:
        logger.info("Checkpoint store not available (agent store backend: %s)", settings.agents.store.backend)

    from agentic_primitives_gateway.routes.teams import get_team_runner, set_team_store

    team_store_cls = _load_class(TEAM_STORE_ALIASES.get(settings.teams.store.backend, settings.teams.store.backend))
    team_store = team_store_cls(**settings.teams.store.config)
    if hasattr(team_store, "bind_agent_store"):
        team_store.bind_agent_store(agent_store)
    if settings.teams.specs:
        await team_store.seed_async(settings.teams.specs)
    set_team_store(team_store)
    get_team_runner().set_stores(agent_store, team_store, agent_runner)

    team_bg = team_store.create_background_run_manager(stale_seconds=600, grace_seconds=60)
    if team_bg:
        from agentic_primitives_gateway.routes.teams import set_team_bg

        set_team_bg(team_bg)
    team_session_reg = team_store.create_session_registry()
    if team_session_reg:
        get_team_runner().set_session_registry(team_session_reg)
    if checkpoint_store and heartbeat:
        get_team_runner().set_checkpoint_store(checkpoint_store, replica_id=heartbeat.replica_id)

    # Initialize auth backend (defaults to noop if not configured)
    auth_cfg = settings.auth
    auth_cls_path = AUTH_BACKEND_ALIASES.get(auth_cfg.backend, auth_cfg.backend)
    auth_cls = _load_class(auth_cls_path)
    auth_kwargs: dict = {}
    if auth_cfg.backend == "api_key":
        auth_kwargs["api_keys"] = auth_cfg.api_keys
    elif auth_cfg.backend == "jwt":
        auth_kwargs.update(auth_cfg.jwt)
    auth_backend: AuthBackend = auth_cls(**auth_kwargs)
    app.state.auth_backend = auth_backend
    logger.info("Auth backend: %s", auth_cfg.backend)

    # Initialize credential resolver
    creds_cfg = settings.credentials
    resolver_cls_path = CREDENTIAL_RESOLVER_ALIASES.get(creds_cfg.resolver, creds_cfg.resolver)
    resolver_cls = _load_class(resolver_cls_path)
    resolver_kwargs: dict = {}
    if creds_cfg.resolver == "oidc":
        from agentic_primitives_gateway.credentials.cache import CredentialCache

        cache = CredentialCache(
            ttl_seconds=creds_cfg.cache.ttl_seconds,
            max_entries=creds_cfg.cache.max_entries,
        )
        resolver_kwargs["cache"] = cache
        # Derive issuer from auth config if available
        if settings.auth.backend == "jwt" and settings.auth.jwt.get("issuer"):
            resolver_kwargs["issuer"] = settings.auth.jwt["issuer"]
        # Share admin credentials with the resolver so it can read user
        # attributes directly from the Admin API (no protocol mappers needed)
        writer_cfg = creds_cfg.writer.config
        if writer_cfg.get("admin_client_id") and writer_cfg.get("admin_client_secret"):
            resolver_kwargs["admin_client_id"] = writer_cfg["admin_client_id"]
            resolver_kwargs["admin_client_secret"] = writer_cfg["admin_client_secret"]
    credential_resolver: CredentialResolver = resolver_cls(**resolver_kwargs)
    app.state.credential_resolver = credential_resolver
    logger.info("Credential resolver: %s", creds_cfg.resolver)

    # Initialize credential writer
    writer_cls_path = CREDENTIAL_WRITER_ALIASES.get(creds_cfg.writer.backend, creds_cfg.writer.backend)
    writer_cls = _load_class(writer_cls_path)
    writer_kwargs = dict(creds_cfg.writer.config)
    if (
        creds_cfg.writer.backend == "keycloak"
        and "issuer" not in writer_kwargs
        and settings.auth.backend == "jwt"
        and settings.auth.jwt.get("issuer")
    ):
        writer_kwargs["issuer"] = settings.auth.jwt["issuer"]
    credential_writer = writer_cls(**writer_kwargs)
    app.state.credential_writer = credential_writer
    credentials.set_credential_writer(credential_writer)
    credentials.set_credential_resolver(credential_resolver)
    logger.info("Credential writer: %s", creds_cfg.writer.backend)

    # Initialize policy enforcer
    enforcer_cfg = settings.enforcement
    enforcer_cls = _load_class(enforcer_cfg.backend)
    enforcer: PolicyEnforcer = enforcer_cls(**enforcer_cfg.config)

    # Auto-provision a scoped engine if supported (Cedar enforcer)
    if hasattr(enforcer, "ensure_engine"):
        engine_id = await enforcer.ensure_engine()
        await _seed_policies(engine_id)

    await enforcer.load_policies()
    if hasattr(enforcer, "start_refresh"):
        enforcer.start_refresh()
    app.state.enforcer = enforcer

    watcher: ConfigWatcher | None = None
    config_path = Settings.config_file_path()
    if config_path:
        watcher = ConfigWatcher(config_path, registry)
        await watcher.start()

    # Recover orphaned runs in the background (don't block server startup)
    if checkpoint_store and heartbeat:
        team_runner_ref = get_team_runner()
        heartbeat.set_runner(agent_runner, team_runner=team_runner_ref)

        async def _initial_recovery() -> None:
            logger.info("Starting orphan recovery scan...")
            try:
                count = await recover_orphaned_runs(
                    checkpoint_store,
                    agent_runner,
                    heartbeat.replica_id,
                    team_runner=team_runner_ref,
                )
                logger.info("Orphan recovery scan complete: %d run(s) recovered", count)
            except Exception:
                logger.exception("Orphan recovery failed")

        app.state._recovery_task = asyncio.create_task(_initial_recovery())
        heartbeat.start_orphan_scanner()
    else:
        logger.info("Orphan recovery disabled (no checkpoint store)")

    yield

    # ── Graceful shutdown ────────────────────────────────────────────
    if heartbeat:
        await heartbeat.stop()

    if watcher is not None:
        await watcher.stop()

    await auth_backend.close()
    await credential_resolver.close()
    await credential_writer.close()
    await enforcer.close()

    if audit_router is not None:
        await audit_router.shutdown()
        set_audit_router(None)


app = FastAPI(
    title="Agentic Primitives Gateway",
    description="Unified API for agent infrastructure primitives",
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware execution order (outermost first):
# CORS → RequestContext → Audit → Auth → CredentialResolution → PolicyEnforcement → handler
# (Starlette runs last-added middleware outermost.)
# AuditMiddleware wraps auth/creds/policy so its http.request event sees the
# final response status, but runs inside RequestContextMiddleware so
# request_id and correlation_id are populated.
app.add_middleware(PolicyEnforcementMiddleware)
app.add_middleware(CredentialResolutionMiddleware)
app.add_middleware(AuthenticationMiddleware)
app.add_middleware(AuditMiddleware)
app.add_middleware(RequestContextMiddleware)


def _resolve_cors_config(origins: list[str]) -> tuple[list[str], bool]:
    """Return the effective ``(allow_origins, allow_credentials)`` for CORS.

    Rejects ``["*", <specific>]`` as invalid (the wildcard is not allowed
    alongside explicit origins) and defuses ``["*"]`` + credentials,
    which browsers reject per the Fetch spec.  Pure function so it can
    be unit-tested without reloading the app.
    """
    wildcard = "*" in origins
    if wildcard and len(origins) > 1:
        raise RuntimeError(
            f"Invalid cors_origins {origins!r} — '*' cannot be combined with explicit origins. Pick one."
        )
    if wildcard:
        logger.warning(
            "cors_origins is ['*']; disabling allow_credentials because "
            "browsers reject that combination per the Fetch spec. "
            "Set cors_origins to an explicit list of origins to enable "
            "credentialed cross-origin requests."
        )
        return ["*"], False
    return list(origins), True


_cors_origins, _cors_credentials = _resolve_cors_config(settings.cors_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> Response:
    logger.warning("ValueError on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ConnectionError)
async def connection_error_handler(request: Request, exc: ConnectionError) -> Response:
    logger.warning("Connection error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": f"Service unavailable: {exc}"},
    )


@app.exception_handler(TimeoutError)
async def timeout_error_handler(request: Request, exc: TimeoutError) -> Response:
    logger.warning("Timeout on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=504,
        content={"detail": f"Gateway timeout: {exc}"},
    )


@app.exception_handler(Exception)
async def general_error_handler(request: Request, exc: Exception) -> Response:
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
app.include_router(llm.router)
app.include_router(tools.router)
app.include_router(identity.router)
app.include_router(code_interpreter.router)
app.include_router(browser.router)
app.include_router(policy.router)
app.include_router(evaluations.router)
app.include_router(agents.router)
app.include_router(teams.router)
app.include_router(a2a.router)
app.include_router(credentials.router)
app.include_router(audit_routes.router)
app.include_router(admin_proposals.router)


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
        file_path = (_STATIC_DIR / full_path).resolve()
        if file_path.is_file() and str(file_path).startswith(str(_STATIC_DIR.resolve())):
            return FileResponse(file_path)
        return FileResponse(_STATIC_DIR / "index.html")
