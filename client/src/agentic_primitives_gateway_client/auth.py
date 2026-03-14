"""JWT authentication helpers for the Agentic Primitives Gateway client.

Provides utilities for obtaining JWT tokens from OIDC providers (Keycloak,
Cognito, Auth0, etc.) using standard OAuth2 flows.

Usage::

    from agentic_primitives_gateway_client.auth import (
        fetch_oidc_token,
        fetch_token_from_env,
    )

    # Direct token fetch (Resource Owner Password Grant)
    token = fetch_oidc_token(
        issuer="https://keycloak.example.com/realms/my-realm",
        client_id="my-app",
        username="admin",
        password="secret",
    )

    # Auto-resolve from environment variables
    token = fetch_token_from_env()  # checks JWT_TOKEN, then OIDC_* / KEYCLOAK_*

    # Use with the platform client
    client = AgenticPlatformClient("http://localhost:8000", auth_token=token)
"""

from __future__ import annotations

import os

import httpx


def fetch_oidc_token(
    issuer: str,
    client_id: str,
    username: str,
    password: str,
    *,
    scopes: str = "openid",
    token_endpoint: str | None = None,
    timeout: float = 10.0,
) -> str:
    """Obtain a JWT access token using the Resource Owner Password Grant.

    Works with any OIDC provider (Keycloak, Cognito, Auth0, etc.).

    Args:
        issuer: OIDC issuer URL (e.g., ``https://keycloak.example.com/realms/my-realm``).
        client_id: OAuth2 client ID (must be a public client with "Direct access grants" enabled).
        username: Username for authentication.
        password: Password for authentication.
        scopes: Space-separated OAuth2 scopes (default: ``"openid"``).
        token_endpoint: Override the token endpoint URL. If not provided,
            defaults to ``{issuer}/protocol/openid-connect/token`` (Keycloak convention).
            For other providers, specify the full token URL.
        timeout: HTTP request timeout in seconds.

    Returns:
        The access token string.

    Raises:
        httpx.HTTPStatusError: If the token request fails.
    """
    if token_endpoint is None:
        token_endpoint = f"{issuer.rstrip('/')}/protocol/openid-connect/token"

    resp = httpx.post(
        token_endpoint,
        data={
            "grant_type": "password",
            "client_id": client_id,
            "username": username,
            "password": password,
            "scope": scopes,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    token: str = resp.json()["access_token"]
    return token


def fetch_client_credentials_token(
    issuer: str,
    client_id: str,
    client_secret: str,
    *,
    scopes: str = "openid",
    token_endpoint: str | None = None,
    timeout: float = 10.0,
) -> str:
    """Obtain a JWT access token using the Client Credentials Grant.

    Useful for machine-to-machine authentication where no user is involved.

    Args:
        issuer: OIDC issuer URL.
        client_id: OAuth2 client ID.
        client_secret: OAuth2 client secret.
        scopes: Space-separated OAuth2 scopes (default: ``"openid"``).
        token_endpoint: Override the token endpoint URL.
        timeout: HTTP request timeout in seconds.

    Returns:
        The access token string.

    Raises:
        httpx.HTTPStatusError: If the token request fails.
    """
    if token_endpoint is None:
        token_endpoint = f"{issuer.rstrip('/')}/protocol/openid-connect/token"

    resp = httpx.post(
        token_endpoint,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scopes,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    token: str = resp.json()["access_token"]
    return token


def fetch_token_from_env(
    *,
    verbose: bool = True,
) -> str | None:
    """Attempt to obtain a JWT token from environment variables.

    Checks the following sources in order:

    1. ``JWT_TOKEN`` — use a pre-existing token directly
    2. ``OIDC_ISSUER`` + ``OIDC_CLIENT_ID`` + ``OIDC_USERNAME`` + ``OIDC_PASSWORD``
       — fetch via Resource Owner Password Grant
    3. ``KEYCLOAK_ISSUER`` + ``KEYCLOAK_CLIENT_ID`` + ``KEYCLOAK_USERNAME`` + ``KEYCLOAK_PASSWORD``
       — same, but with Keycloak-specific env var names

    Args:
        verbose: Print status messages to stdout.

    Returns:
        The access token string, or ``None`` if no credentials are configured.
    """
    # 1. Direct token
    token = os.environ.get("JWT_TOKEN")
    if token:
        if verbose:
            print(f"JWT auth: using JWT_TOKEN ({len(token)} chars)")
        return token

    # 2. Generic OIDC env vars
    oidc_issuer = os.environ.get("OIDC_ISSUER", "")
    oidc_username = os.environ.get("OIDC_USERNAME", "")
    oidc_password = os.environ.get("OIDC_PASSWORD", "")
    if oidc_issuer and oidc_username and oidc_password:
        token = fetch_oidc_token(
            issuer=oidc_issuer,
            client_id=os.environ.get("OIDC_CLIENT_ID", "agentic-gateway"),
            username=oidc_username,
            password=oidc_password,
        )
        if verbose:
            print(f"JWT auth: authenticated as {oidc_username} via OIDC ({oidc_issuer})")
        return token

    # 3. Keycloak-specific env vars (backwards compatibility)
    kc_issuer = os.environ.get("KEYCLOAK_ISSUER", "")
    kc_username = os.environ.get("KEYCLOAK_USERNAME", "")
    kc_password = os.environ.get("KEYCLOAK_PASSWORD", "")
    if kc_issuer and kc_username and kc_password:
        token = fetch_oidc_token(
            issuer=kc_issuer,
            client_id=os.environ.get("KEYCLOAK_CLIENT_ID", "agentic-gateway"),
            username=kc_username,
            password=kc_password,
        )
        if verbose:
            print(f"JWT auth: authenticated as {kc_username} via Keycloak")
        return token

    if verbose:
        print("JWT auth: no credentials configured")
    return None
