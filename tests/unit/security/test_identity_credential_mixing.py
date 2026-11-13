"""Caller and operator identity credentials are never mixed.

``never`` must not hand a caller the operator's secret, and ``fallback`` must
not let a caller pick the realm / tenant / client the operator's secret is
used against: a caller's partial set is used whole (missing fields ``None``)
and the operator's set only applies when the caller supplied nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.context import set_service_credentials
from agentic_primitives_gateway.models.enums import ServerCredentialMode
from agentic_primitives_gateway.primitives.identity.entra import EntraIdentityProvider
from agentic_primitives_gateway.primitives.identity.keycloak import KeycloakIdentityProvider
from agentic_primitives_gateway.primitives.identity.okta import OktaIdentityProvider

_CALLER_MODES = [ServerCredentialMode.NEVER, ServerCredentialMode.FALLBACK]


@pytest.fixture(params=_CALLER_MODES, ids=lambda m: m.value)
def mode(request: pytest.FixtureRequest) -> Iterator[ServerCredentialMode]:
    prev = settings.allow_server_credentials
    settings.allow_server_credentials = request.param
    yield request.param
    settings.allow_server_credentials = prev
    set_service_credentials({})


def _keycloak() -> KeycloakIdentityProvider:
    return KeycloakIdentityProvider(
        server_url="https://kc.internal", realm="op-realm", client_id="op-client", client_secret="op-secret"
    )


def test_keycloak_caller_set_never_gets_operator_secret(mode):
    set_service_credentials({"keycloak": {"realm": "caller-realm", "client_id": "caller-client"}})
    with (
        patch("agentic_primitives_gateway.primitives.identity.keycloak.KeycloakOpenID") as openid,
        patch("agentic_primitives_gateway.primitives.identity.keycloak.KeycloakAdmin") as admin,
    ):
        _keycloak()._get_openid()
        _keycloak()._get_admin()
    for cls in (openid, admin):
        kwargs = cls.call_args.kwargs
        assert kwargs["client_secret_key"] is None
        assert kwargs["client_id"] == "caller-client"
        assert kwargs["server_url"] == "https://kc.internal"  # endpoint stays operator-owned


def test_keycloak_caller_realm_alone_cannot_borrow_operator_client(mode):
    set_service_credentials({"keycloak": {"realm": "other-realm"}})
    with (
        patch("agentic_primitives_gateway.primitives.identity.keycloak.KeycloakOpenID") as openid,
        pytest.raises(ValueError, match="realm and client_id are required"),
    ):
        _keycloak()._get_openid()
    openid.assert_not_called()


def test_keycloak_fallback_without_caller_creds_uses_operator_set():
    prev = settings.allow_server_credentials
    settings.allow_server_credentials = ServerCredentialMode.FALLBACK
    set_service_credentials({})
    try:
        with patch("agentic_primitives_gateway.primitives.identity.keycloak.KeycloakOpenID") as openid:
            _keycloak()._get_openid()
    finally:
        settings.allow_server_credentials = prev
    assert openid.call_args.kwargs == {
        "server_url": "https://kc.internal",
        "realm_name": "op-realm",
        "client_id": "op-client",
        "client_secret_key": "op-secret",
    }


def test_okta_caller_set_never_gets_operator_secret_or_api_token(mode):
    set_service_credentials({"okta": {"client_id": "caller-client"}})
    provider = OktaIdentityProvider(
        domain="org.okta.com", client_id="op-client", client_secret="op-secret", api_token="op-ssws"
    )
    assert provider._client_auth() == ("caller-client", "")
    assert provider._base_url() == "https://org.okta.com"
    with pytest.raises(ValueError, match="API token required"):
        provider._admin_headers()


def _entra() -> EntraIdentityProvider:
    return EntraIdentityProvider(tenant_id="op-tenant", client_id="op-client", client_secret="op-secret")


def test_entra_caller_set_never_gets_operator_secret(mode):
    set_service_credentials({"entra": {"tenant_id": "caller-tenant", "client_id": "caller-client"}})
    with patch("agentic_primitives_gateway.primitives.identity.entra.msal.ConfidentialClientApplication") as app:
        _entra()._get_app()
    kwargs = app.call_args.kwargs
    assert kwargs["client_credential"] is None
    assert kwargs["client_id"] == "caller-client"
    assert kwargs["authority"] == "https://login.microsoftonline.com/caller-tenant"


def test_entra_caller_tenant_alone_cannot_borrow_operator_client(mode):
    set_service_credentials({"entra": {"tenant_id": "other-tenant"}})
    with (
        patch("agentic_primitives_gateway.primitives.identity.entra.msal.ConfidentialClientApplication") as app,
        pytest.raises(ValueError, match="tenant_id and client_id are required"),
    ):
        _entra()._get_app()
    app.assert_not_called()
