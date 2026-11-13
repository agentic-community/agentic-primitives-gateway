"""Bind AgentCore SDK clients to a request-scoped boto3 session.

The ``bedrock_agentcore`` Identity and Browser clients build their boto3
clients with the module-global ``boto3.client(...)`` — the process's ambient
credential chain — and accept only a region.  Passing just
``session.region_name`` therefore silently discards the caller's credentials
that ``get_boto3_session()`` validated, and every call runs as the gateway's
own AWS identity.  ``rebind_agentcore_clients`` swaps in clients created from
the request session.
"""

from __future__ import annotations

from typing import Any

from bedrock_agentcore._utils.endpoints import get_control_plane_endpoint, get_data_plane_endpoint
from botocore.config import Config


def _config_of(client: Any) -> Config | None:
    """The botocore ``Config`` the SDK gave ``client`` (timeouts, user agent, retries)."""
    config = getattr(getattr(client, "meta", None), "config", None)
    return config if isinstance(config, Config) else None


def rebind_agentcore_clients(
    sdk_client: Any,
    session: Any,
    *,
    control_plane: str,
    data_plane: str,
    region: str | None = None,
) -> None:
    """Replace an AgentCore SDK object's control/data-plane clients with ``session`` clients.

    ``control_plane`` / ``data_plane`` name the attributes holding the
    ``bedrock-agentcore-control`` / ``bedrock-agentcore`` clients.  Endpoints
    match what the SDK itself would use for ``region`` (default: the
    session's region; pass the SDK object's region for long-lived sessions,
    which are regional).

    Raises ``RuntimeError`` if the SDK object lacks either attribute — an SDK
    upgrade renaming them must fail loudly rather than silently leave the
    ambient-credential clients in place.
    """
    for attr in (control_plane, data_plane):
        if not hasattr(sdk_client, attr):
            raise RuntimeError(
                f"{type(sdk_client).__name__} has no {attr!r}; bedrock-agentcore SDK layout changed — "
                "refusing to run with ambient AWS credentials"
            )
    region = region or session.region_name
    for attr, service, endpoint in (
        (control_plane, "bedrock-agentcore-control", get_control_plane_endpoint(region)),
        (data_plane, "bedrock-agentcore", get_data_plane_endpoint(region)),
    ):
        # Keep the SDK's client config — e.g. the code interpreter's
        # 300s data-plane read timeout — on the replacement client.
        config = _config_of(getattr(sdk_client, attr))
        setattr(
            sdk_client,
            attr,
            session.client(service, region_name=region, endpoint_url=endpoint, config=config),
        )
