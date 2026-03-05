from __future__ import annotations

import logging
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agentic_primitives_gateway.enforcement.base import PolicyEnforcer

logger = logging.getLogger(__name__)

# Paths exempt from enforcement (prefix match)
_EXEMPT_PREFIXES = (
    "/healthz",
    "/readyz",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/providers",
    "/api/v1/policy",
)


def _build_action_rules(
    routes: list[Route],
) -> list[tuple[str, re.Pattern[str], str]]:
    """Build action rules from the app's registered routes.

    Introspects FastAPI/Starlette routes and derives Cedar actions from
    the router prefix (primitive) and endpoint function name. This means
    new routes are automatically enforced without updating a static list.

    Each route ``/api/v1/{primitive}/...`` with endpoint ``my_func``
    produces action ``{primitive}:my_func``.
    """
    rules: list[tuple[str, re.Pattern[str], str]] = []
    for route in routes:
        if not isinstance(route, Route):
            # Skip Mount, WebSocket, etc. — recurse into sub-applications
            sub_routes = getattr(route, "routes", None)
            if sub_routes:
                rules.extend(_build_action_rules(sub_routes))
            continue

        path = route.path
        if not path.startswith("/api/v1/"):
            continue

        methods = route.methods or set()
        endpoint = route.endpoint
        if endpoint is None:
            continue

        # Derive primitive from path: /api/v1/{primitive}/...
        remainder = path.removeprefix("/api/v1/")
        primitive_segment = remainder.split("/", 1)[0]
        # Normalize: "code-interpreter" → "code_interpreter"
        primitive = primitive_segment.replace("-", "_")

        # Action = primitive:endpoint_name (e.g., "memory:store_memory")
        action = f"{primitive}:{endpoint.__name__}"

        # Convert FastAPI path params to regex: {param} → [^/]+, {param:path} → .+
        pattern_str = re.sub(r"\{[^}]+:path\}", ".+", path)
        pattern_str = re.sub(r"\{[^}]+\}", "[^/]+", pattern_str)
        pattern = re.compile(f"^{pattern_str}$")

        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            rules.append((method, pattern, action))

    return rules


# Module-level cache: built once per app on first request
_cached_rules: list[tuple[str, re.Pattern[str], str]] | None = None
_cached_app_id: int | None = None


def _get_action_rules(app: object) -> list[tuple[str, re.Pattern[str], str]]:
    """Return cached action rules, rebuilding if the app instance changed."""
    global _cached_rules, _cached_app_id  # noqa: PLW0603
    app_id = id(app)
    if _cached_rules is None or _cached_app_id != app_id:
        routes = getattr(app, "routes", [])
        _cached_rules = _build_action_rules(routes)
        _cached_app_id = app_id
        logger.info("Built %d enforcement action rules from app routes", len(_cached_rules))
    return _cached_rules


def _resolve_principal(request: Request) -> str:
    """Derive the Cedar principal from request headers."""
    agent_id = request.headers.get("x-agent-id")
    if agent_id:
        return f'Agent::"{agent_id}"'

    # Fall back to service credential name
    for header_name in request.headers:
        if header_name.startswith("x-cred-"):
            parts = header_name.removeprefix("x-cred-").split("-", 1)
            if parts:
                return f'Service::"{parts[0]}"'

    aws_key = request.headers.get("x-aws-access-key-id")
    if aws_key:
        return f'AWSPrincipal::"{aws_key}"'

    return 'Agent::"anonymous"'


def _resolve_action(app: object, method: str, path: str) -> str | None:
    """Map HTTP method + path to a Cedar action string, or None if unmapped."""
    for rule_method, pattern, action in _get_action_rules(app):
        if method == rule_method and pattern.match(path):
            return action
    return None


def _resolve_resource(path: str) -> str:
    """Extract a resource identifier from the URL path."""
    # Strip /api/v1/ prefix and return the rest
    if path.startswith("/api/v1/"):
        return path[len("/api/v1/") :]
    return path


class PolicyEnforcementMiddleware(BaseHTTPMiddleware):
    """Evaluate incoming requests against the configured PolicyEnforcer.

    Registered after ``RequestContextMiddleware`` in code, so it runs
    inside the request context stack.  Looks up the enforcer from
    ``request.app.state.enforcer``.

    Action rules are built automatically from the app's registered routes
    on the first request.  New routes added to the app are picked up
    without any changes to this middleware.

    Exempt paths (health, docs, policy CRUD, provider discovery) are
    never enforced.  Unknown routes (no action mapping) pass through.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        enforcer: PolicyEnforcer | None = getattr(request.app.state, "enforcer", None)
        if enforcer is None:
            return await call_next(request)

        path = request.url.path
        for prefix in _EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        action = _resolve_action(request.app, request.method, path)
        if action is None:
            # Unknown route — not enforced
            return await call_next(request)

        principal = _resolve_principal(request)
        resource = _resolve_resource(path)

        allowed = await enforcer.authorize(
            principal=principal,
            action=action,
            resource=resource,
        )

        if not allowed:
            logger.warning(
                "Policy denied: principal=%s action=%s resource=%s",
                principal,
                action,
                resource,
            )
            return JSONResponse(
                status_code=403,
                content={"detail": "Forbidden by policy"},
            )

        return await call_next(request)
