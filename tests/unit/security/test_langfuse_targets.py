"""Langfuse SDK, REST and health requests stay on the operator-selected target."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from agentic_primitives_gateway.agents.checkpoint_utils import restore_auth_context
from agentic_primitives_gateway.auth.middleware import AuthenticationMiddleware
from agentic_primitives_gateway.auth.noop import NoopAuthBackend
from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.context import set_authenticated_principal, set_service_credentials
from agentic_primitives_gateway.credentials.middleware import CredentialResolutionMiddleware
from agentic_primitives_gateway.credentials.oidc import OidcCredentialResolver
from agentic_primitives_gateway.middleware import RequestContextMiddleware
from agentic_primitives_gateway.models.enums import ServerCredentialMode
from agentic_primitives_gateway.primitives.evaluations.langfuse import LangfuseEvaluationsProvider
from agentic_primitives_gateway.primitives.observability.langfuse import LangfuseObservabilityProvider

pytestmark = pytest.mark.usefixtures("langfuse_pairs_valid")

_REDIRECT = "http://redirect.invalid:8081"


@pytest.fixture(params=list(ServerCredentialMode), ids=lambda m: m.value)
def mode(request: pytest.FixtureRequest) -> Iterator[ServerCredentialMode]:
    prev = settings.allow_server_credentials
    settings.allow_server_credentials = request.param
    yield request.param
    settings.allow_server_credentials = prev
    set_service_credentials({})
    set_authenticated_principal(None)


@pytest.fixture(params=["header", "oidc", "checkpoint"])
def channel(request: pytest.FixtureRequest) -> str:
    return request.param


async def _observability() -> list[str]:
    provider = LangfuseObservabilityProvider(public_key="op-pk", secret_key="op-sk", base_url="https://lf.internal")
    targets: list[str] = []
    with patch("agentic_primitives_gateway.primitives.observability.langfuse.Langfuse") as sdk:
        await provider._resolve_client()
        targets.append(sdk.call_args.kwargs["base_url"])
    with patch("agentic_primitives_gateway.primitives.observability.langfuse.httpx.get") as get:
        get.return_value.json.return_value = {"data": []}
        await provider.list_scores("0123456789abcdef0123456789abcdef")
        targets.append(get.call_args.args[0])
    return targets


async def _evaluations() -> list[str]:
    provider = LangfuseEvaluationsProvider(public_key="op-pk", secret_key="op-sk", base_url="https://lf.internal")
    targets: list[str] = []
    with patch("agentic_primitives_gateway.primitives.evaluations.langfuse.FernLangfuse") as rest:
        provider._resolve_rest_client()
        targets.append(rest.call_args.kwargs["base_url"])
    with patch("langfuse.Langfuse") as sdk:
        await provider._resolve_sdk_client()
        targets.append(sdk.call_args.kwargs["base_url"])
        sdk.return_value.auth_check.return_value = True
        await provider.healthcheck()
        targets.append(sdk.call_args.kwargs["base_url"])
    return targets


@pytest.mark.asyncio
@pytest.mark.parametrize("sink", [_observability, _evaluations], ids=["observability", "evaluations"])
async def test_langfuse_target_from_operator_for_each_ingress(
    mode: ServerCredentialMode, channel: str, sink: Callable[[], Awaitable[list[str]]]
) -> None:
    creds = {"langfuse": {"base_url": _REDIRECT, "public_key": "pk", "secret_key": "sk"}}
    resolver = OidcCredentialResolver(userinfo_url="https://idp.invalid/userinfo")
    attrs = {f"apg.{svc}.{k}": v for svc, kv in creds.items() for k, v in kv.items()}
    resolver._fetch_userinfo = AsyncMock(return_value=attrs if channel == "oidc" else {})  # type: ignore[method-assign]
    app = FastAPI()
    result: dict[str, list[str]] = {}

    @app.get("/sink")
    async def run_sink() -> dict[str, bool]:
        if channel == "checkpoint":
            restore_auth_context({"principal": {"id": "mallory"}, "service_credentials": creds})
        result["targets"] = await sink()
        return {"ok": True}

    app.add_middleware(CredentialResolutionMiddleware)
    app.add_middleware(AuthenticationMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.state.auth_backend = NoopAuthBackend()
    app.state.credential_resolver = resolver
    headers = {"Authorization": "Bearer user-jwt"}
    if channel == "header":
        for key, value in creds["langfuse"].items():
            headers[f"X-Cred-Langfuse-{key.replace('_', '-')}"] = value
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gw") as client:
        resp = await client.get("/sink", headers=headers)
    assert resp.status_code == 200, resp.text
    assert result["targets"]
    for target in result["targets"]:
        assert target.startswith("https://lf.internal"), target
        assert "redirect.invalid" not in target
