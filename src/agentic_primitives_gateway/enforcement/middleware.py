from __future__ import annotations

import logging
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from agentic_primitives_gateway.context import get_authenticated_principal
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
    "/ui",
    "/api/v1/providers",
    "/api/v1/policy",
    "/auth/config",
)


def _build_action_rules(
    routes: list[Route],
) -> list[tuple[str, re.Pattern[str], str]]:
    """Build Cedar action rules by introspecting the app's registered routes.

    This avoids maintaining a static mapping of paths → Cedar actions. Instead,
    we derive actions automatically from the route structure:

      /api/v1/memory/{namespace}/{key}  +  endpoint=retrieve_memory
      → action = "memory:retrieve_memory"

    The result is a list of (HTTP_METHOD, compiled_regex, cedar_action) tuples.
    At request time, _resolve_action iterates these to find a match.

    Path parameters are converted to regex patterns so that
    /api/v1/memory/{namespace}/{key} matches /api/v1/memory/my-ns/my-key.
    FastAPI's {param:path} (catch-all) becomes .+ ; regular {param} becomes [^/]+.

    Non-Route entries (Mount, WebSocket) are recursed into to handle sub-apps.
    HEAD/OPTIONS are skipped (no enforcement needed for CORS preflight).
    """
    rules: list[tuple[str, re.Pattern[str], str]] = []
    for route in routes:
        if not isinstance(route, Route):
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

        # Derive primitive from first path segment: /api/v1/{primitive}/...
        remainder = path.removeprefix("/api/v1/")
        primitive_segment = remainder.split("/", 1)[0]
        primitive = primitive_segment.replace("-", "_")  # "code-interpreter" → "code_interpreter"

        action = f"{primitive}:{endpoint.__name__}"

        # Convert FastAPI path params to regex for matching at request time
        pattern_str = re.sub(r"\{[^}]+:path\}", ".+", path)  # {name:path} → .+
        pattern_str = re.sub(r"\{[^}]+\}", "[^/]+", pattern_str)  # {param} → [^/]+
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
    global _cached_rules, _cached_app_id
    app_id = id(app)
    if _cached_rules is None or _cached_app_id != app_id:
        routes = getattr(app, "routes", [])
        _cached_rules = _build_action_rules(routes)
        _cached_app_id = app_id
        logger.info("Built %d enforcement action rules from app routes", len(_cached_rules))
    return _cached_rules


def _resolve_principal(request: Request) -> str:
    """Derive the Cedar principal from the authenticated principal in context.

    For authenticated requests (non-exempt paths), the auth middleware always
    sets a non-anonymous principal. This function reads it from context.

    For exempt paths (health, docs, UI), the principal may be anonymous.
    In that case we fall back to header-based derivation for Cedar evaluation.
    The final ``Agent::"anonymous"`` is only reachable on exempt paths where
    no identifying headers are present — these paths are skipped by enforcement
    anyway (checked in ``_EXEMPT_PREFIXES``).
    """
    principal = get_authenticated_principal()
    if principal is not None and not principal.is_anonymous:
        type_label = principal.type.capitalize()
        return f'{type_label}::"{principal.id}"'

    # Header-based fallback (exempt paths only — non-exempt paths always
    # have a real principal from auth middleware, or got 401 already)
    agent_id = request.headers.get("x-agent-id")
    if agent_id:
        return f'Agent::"{agent_id}"'

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
