"""Verify request endpoint keys never change a provider's outbound target.

For every provider that pairs an endpoint with credentials, send caller
credentials carrying an endpoint override through each real ingress channel —
``X-Cred-*`` headers (``RequestContextMiddleware``), OIDC ``apg.*`` attributes
(``CredentialResolutionMiddleware`` + ``OidcCredentialResolver``), and
checkpoint restore — under every ``allow_server_credentials`` mode, then
inspect the actual target handed to the SDK / HTTP client inside the request.
It must always be the operator-configured endpoint.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

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
from agentic_primitives_gateway.primitives.browser.selenium_grid import SeleniumGridBrowserProvider
from agentic_primitives_gateway.primitives.evaluations.langfuse import LangfuseEvaluationsProvider
from agentic_primitives_gateway.primitives.identity.keycloak import KeycloakIdentityProvider
from agentic_primitives_gateway.primitives.identity.okta import OktaIdentityProvider
from agentic_primitives_gateway.primitives.observability.langfuse import LangfuseObservabilityProvider
from agentic_primitives_gateway.primitives.tools.agentcore import AgentCoreGatewayProvider
from agentic_primitives_gateway.primitives.tools.mcp_registry import MCPRegistryProvider

# The Langfuse SDK is mocked; skip the binding's network verification of key pairs.
pytestmark = pytest.mark.usefixtures("langfuse_pairs_valid")

_REDIRECT = "http://redirect.invalid:8081"


async def _run_through_gateway(channel: str, creds: dict[str, dict[str, str]], sink: Sink) -> list[str]:
    """Deliver ``creds`` via ``channel`` through the real middleware stack and run ``sink`` in the request."""
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
        for svc, kv in creds.items():
            for k, v in kv.items():
                headers[f"X-Cred-{svc}-{k.replace('_', '-')}"] = v
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gw") as client:
        resp = await client.get("/sink", headers=headers)
    assert resp.status_code == 200, resp.text
    return result["targets"]


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


def _http_client_mock() -> MagicMock:
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    resp = MagicMock(status_code=200, headers={})
    resp.json.return_value = {"servers": [], "data": [], "result": {"tools": []}}
    resp.text = ""
    client.get.return_value = resp
    client.post.return_value = resp
    return client


# Each sink: request-supplied endpoint and credentials, with the outbound target returned.
Sink = Callable[[], Awaitable[list[str]]]


async def _langfuse_observability() -> list[str]:
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


async def _langfuse_evaluations() -> list[str]:
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


async def _keycloak() -> list[str]:
    provider = KeycloakIdentityProvider(
        server_url="https://kc.internal", realm="r", client_id="op-client", client_secret="op-secret"
    )
    with (
        patch("agentic_primitives_gateway.primitives.identity.keycloak.KeycloakOpenID") as openid,
        patch("agentic_primitives_gateway.primitives.identity.keycloak.KeycloakAdmin") as admin,
    ):
        provider._get_openid()
        provider._get_admin()
    return [openid.call_args.kwargs["server_url"], admin.call_args.kwargs["server_url"]]


async def _okta() -> list[str]:
    provider = OktaIdentityProvider(domain="org.okta.internal", client_id="op-client", client_secret="op-secret")
    return [provider._token_url(), provider._authorize_url()]


async def _selenium() -> list[str]:
    provider = SeleniumGridBrowserProvider(hub_url="http://grid.internal:4444", browser="chrome")
    with patch("agentic_primitives_gateway.primitives.browser.selenium_grid.webdriver") as wd:
        wd.Remote.return_value = MagicMock(session_id="wd-1")
        wd.ChromeOptions = MagicMock
        await provider.start_session(session_id="s1")
    return [wd.Remote.call_args.kwargs["command_executor"]]


async def _mcp_registry() -> list[str]:
    provider = MCPRegistryProvider(base_url="https://registry.internal", token="op-token")
    client = _http_client_mock()
    with patch("httpx.Client", return_value=client):
        await provider.list_servers()
        await provider.healthcheck()
    return [c.args[0] for c in client.get.call_args_list]


async def _agentcore_gateway() -> list[str]:
    provider = AgentCoreGatewayProvider(gateway_url="https://gw.internal/mcp")
    client = _http_client_mock()
    with patch("httpx.Client", return_value=client):
        await provider.list_tools()
    return [c.args[0] for c in client.post.call_args_list]


_SINKS: list[tuple[str, dict[str, dict[str, str]], Sink, str]] = [
    (
        "langfuse-observability",
        {"langfuse": {"base_url": _REDIRECT, "public_key": "pk", "secret_key": "sk"}},
        _langfuse_observability,
        "https://lf.internal",
    ),
    (
        "langfuse-evaluations",
        {"langfuse": {"base_url": _REDIRECT, "public_key": "pk", "secret_key": "sk"}},
        _langfuse_evaluations,
        "https://lf.internal",
    ),
    (
        "keycloak",
        {"keycloak": {"server_url": _REDIRECT, "realm": "r", "client_id": "c", "client_secret": "s"}},
        _keycloak,
        "https://kc.internal",
    ),
    (
        "okta",
        {"okta": {"domain": "redirect.invalid", "client_id": "c", "client_secret": "s"}},
        _okta,
        "https://org.okta.internal/",
    ),
    (
        "selenium",
        {"selenium": {"hub_url": _REDIRECT, "browser": "chrome"}},
        _selenium,
        "http://grid.internal:4444",
    ),
    (
        "mcp-registry",
        {"mcp_registry": {"url": _REDIRECT, "token": "user-token"}},
        _mcp_registry,
        "https://registry.internal/",
    ),
    (
        "agentcore-gateway",
        {"agentcore": {"gateway_url": _REDIRECT, "gateway_id": "redirect.invalid/#", "gateway_token": "t"}},
        _agentcore_gateway,
        "https://gw.internal/mcp",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("creds", "sink", "operator_target"), [s[1:] for s in _SINKS], ids=[s[0] for s in _SINKS])
async def test_caller_endpoint_never_reaches_sink(
    mode: ServerCredentialMode,
    channel: str,
    creds: dict[str, dict[str, str]],
    sink: Sink,
    operator_target: str,
) -> None:
    targets = await _run_through_gateway(channel, creds, sink)
    assert targets, "sink made no outbound call"
    for target in targets:
        assert target.startswith(operator_target), target
        assert "redirect.invalid" not in target


def test_checkpoint_restore_drops_endpoint_keys_and_audits() -> None:
    """Pre-fix checkpoints may carry caller URLs; restore must not reinstate them."""
    with patch("agentic_primitives_gateway.credentials.security.emit_credential_key_denied") as emit:
        restore_auth_context(
            {
                "principal": {"id": "mallory"},
                "service_credentials": {
                    "langfuse": {"base_url": _REDIRECT, "public_key": "pk"},
                    "selenium": {"hub_url": _REDIRECT},
                },
            }
        )
    from agentic_primitives_gateway.context import get_all_service_credentials

    try:
        assert get_all_service_credentials() == {"langfuse": {"public_key": "pk"}}
        assert sorted(c.kwargs["key"] for c in emit.call_args_list) == ["base_url", "hub_url"]
        assert {c.kwargs["source"] for c in emit.call_args_list} == {"checkpoint"}
    finally:
        set_service_credentials({})
        set_authenticated_principal(None)
