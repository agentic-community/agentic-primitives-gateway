"""Request context middleware — extracts credentials and routing from headers."""

from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from agentic_primitives_gateway.context import (
    AWSCredentials,
    set_aws_credentials,
    set_provider_overrides,
    set_request_id,
    set_service_credentials,
)
from agentic_primitives_gateway.registry import PRIMITIVES


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
