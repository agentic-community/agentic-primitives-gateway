from __future__ import annotations

import logging
import re

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

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

# Pre-compiled action mapping table: (method, regex) → action string
# Order matters — first match wins.
_ACTION_RULES: list[tuple[str, re.Pattern[str], str]] = [
    # memory
    ("POST", re.compile(r"^/api/v1/memory/[^/]+/search$"), "memory:search"),
    ("POST", re.compile(r"^/api/v1/memory/[^/]+$"), "memory:store"),
    ("GET", re.compile(r"^/api/v1/memory/[^/]+/[^/]+$"), "memory:recall"),
    ("GET", re.compile(r"^/api/v1/memory/[^/]+$"), "memory:list"),
    ("DELETE", re.compile(r"^/api/v1/memory/"), "memory:delete"),
    ("GET", re.compile(r"^/api/v1/memory/"), "memory:read"),
    ("POST", re.compile(r"^/api/v1/memory/"), "memory:write"),
    # gateway
    ("POST", re.compile(r"^/api/v1/gateway/completions$"), "gateway:completions"),
    ("GET", re.compile(r"^/api/v1/gateway/models$"), "gateway:models"),
    # tools
    ("POST", re.compile(r"^/api/v1/tools/.+/invoke$"), "tools:invoke"),
    ("GET", re.compile(r"^/api/v1/tools/search$"), "tools:search"),
    ("POST", re.compile(r"^/api/v1/tools$"), "tools:register"),
    ("GET", re.compile(r"^/api/v1/tools$"), "tools:list"),
    ("GET", re.compile(r"^/api/v1/tools/"), "tools:get"),
    ("DELETE", re.compile(r"^/api/v1/tools/"), "tools:delete"),
    # identity
    ("POST", re.compile(r"^/api/v1/identity/token$"), "identity:token"),
    ("POST", re.compile(r"^/api/v1/identity/api-key$"), "identity:api_key"),
    ("POST", re.compile(r"^/api/v1/identity/workload-token$"), "identity:workload_token"),
    ("POST", re.compile(r"^/api/v1/identity/auth/complete$"), "identity:auth_complete"),
    ("GET", re.compile(r"^/api/v1/identity/"), "identity:read"),
    ("POST", re.compile(r"^/api/v1/identity/"), "identity:write"),
    ("PUT", re.compile(r"^/api/v1/identity/"), "identity:write"),
    ("DELETE", re.compile(r"^/api/v1/identity/"), "identity:delete"),
    # code_interpreter
    ("POST", re.compile(r"^/api/v1/code-interpreter/sessions/[^/]+/execute$"), "code_interpreter:execute"),
    ("POST", re.compile(r"^/api/v1/code-interpreter/sessions/[^/]+/files$"), "code_interpreter:upload"),
    ("GET", re.compile(r"^/api/v1/code-interpreter/sessions/[^/]+/files/"), "code_interpreter:download"),
    ("POST", re.compile(r"^/api/v1/code-interpreter/sessions$"), "code_interpreter:create_session"),
    ("DELETE", re.compile(r"^/api/v1/code-interpreter/sessions/"), "code_interpreter:delete_session"),
    ("GET", re.compile(r"^/api/v1/code-interpreter/"), "code_interpreter:read"),
    # browser
    ("POST", re.compile(r"^/api/v1/browser/sessions/[^/]+/navigate$"), "browser:navigate"),
    ("POST", re.compile(r"^/api/v1/browser/sessions/[^/]+/click$"), "browser:click"),
    ("POST", re.compile(r"^/api/v1/browser/sessions/[^/]+/type$"), "browser:type"),
    ("POST", re.compile(r"^/api/v1/browser/sessions/[^/]+/evaluate$"), "browser:evaluate"),
    ("GET", re.compile(r"^/api/v1/browser/sessions/[^/]+/screenshot$"), "browser:screenshot"),
    ("GET", re.compile(r"^/api/v1/browser/sessions/[^/]+/content$"), "browser:content"),
    ("POST", re.compile(r"^/api/v1/browser/sessions$"), "browser:create_session"),
    ("DELETE", re.compile(r"^/api/v1/browser/sessions/"), "browser:delete_session"),
    ("GET", re.compile(r"^/api/v1/browser/"), "browser:read"),
    # observability
    ("POST", re.compile(r"^/api/v1/observability/flush$"), "observability:flush"),
    ("POST", re.compile(r"^/api/v1/observability/traces/[^/]+/generations$"), "observability:generation"),
    ("POST", re.compile(r"^/api/v1/observability/traces/[^/]+/scores$"), "observability:score"),
    ("POST", re.compile(r"^/api/v1/observability/traces$"), "observability:trace"),
    ("POST", re.compile(r"^/api/v1/observability/logs$"), "observability:log"),
    ("GET", re.compile(r"^/api/v1/observability/"), "observability:read"),
    ("PUT", re.compile(r"^/api/v1/observability/"), "observability:write"),
    # evaluations
    ("POST", re.compile(r"^/api/v1/evaluations/evaluate$"), "evaluations:evaluate"),
    ("POST", re.compile(r"^/api/v1/evaluations/"), "evaluations:write"),
    ("GET", re.compile(r"^/api/v1/evaluations/"), "evaluations:read"),
    ("PUT", re.compile(r"^/api/v1/evaluations/"), "evaluations:write"),
    ("DELETE", re.compile(r"^/api/v1/evaluations/"), "evaluations:delete"),
    # agents
    ("POST", re.compile(r"^/api/v1/agents/[^/]+/chat$"), "agents:chat"),
    ("POST", re.compile(r"^/api/v1/agents$"), "agents:create"),
    ("GET", re.compile(r"^/api/v1/agents$"), "agents:list"),
    ("GET", re.compile(r"^/api/v1/agents/"), "agents:get"),
    ("PUT", re.compile(r"^/api/v1/agents/"), "agents:update"),
    ("DELETE", re.compile(r"^/api/v1/agents/"), "agents:delete"),
]


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


def _resolve_action(method: str, path: str) -> str | None:
    """Map HTTP method + path to a Cedar action string, or None if unmapped."""
    for rule_method, pattern, action in _ACTION_RULES:
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

        action = _resolve_action(request.method, path)
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
