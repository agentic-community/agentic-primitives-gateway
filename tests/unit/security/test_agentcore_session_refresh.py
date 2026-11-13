"""Long-lived AgentCore sessions use the *current* request's AWS credentials.

Browser / code-interpreter sessions outlive the request that started them
(and its possibly short-lived STS credentials).  Every later AWS call must be
re-signed with the credentials in force now, and a failed stop must keep the
session record so it can be retried.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest

from agentic_primitives_gateway.context import AWSCredentials, set_aws_credentials
from agentic_primitives_gateway.primitives.browser.agentcore import AgentCoreBrowserProvider
from agentic_primitives_gateway.primitives.code_interpreter.agentcore import AgentCoreCodeInterpreterProvider


@pytest.fixture(autouse=True)
def _reset_creds() -> Iterator[None]:
    yield
    set_aws_credentials(None)


def _as(key: str) -> None:
    set_aws_credentials(AWSCredentials(access_key_id=key, secret_access_key="s", region="us-west-2"))


def _signing_key(boto_client) -> str:
    return boto_client._request_signer._credentials.access_key


@pytest.mark.asyncio
async def test_code_interpreter_rebinds_to_current_credentials_each_call():
    provider = AgentCoreCodeInterpreterProvider(region="us-east-1")
    sdk = MagicMock(region="us-east-1")
    sdk.execute_code.return_value = {}
    provider._sessions["s1"] = {"client": sdk, "created_at": "t", "language": "python"}

    _as("AKIAFIRST")
    await provider.execute("s1", "1")
    assert _signing_key(sdk.data_plane_client) == "AKIAFIRST"

    _as("AKIAREFRESHED")
    await provider.execute("s1", "2")
    assert _signing_key(sdk.data_plane_client) == "AKIAREFRESHED"
    assert sdk.data_plane_client.meta.region_name == "us-east-1"  # session stays in its region


@pytest.mark.asyncio
async def test_code_interpreter_failed_stop_keeps_session():
    provider = AgentCoreCodeInterpreterProvider()
    sdk = MagicMock(region="us-east-1")
    sdk.stop.side_effect = RuntimeError("ExpiredToken")
    provider._sessions["s1"] = {"client": sdk, "created_at": "t", "language": "python"}
    _as("AKIACALLER")

    with pytest.raises(RuntimeError):
        await provider.stop_session("s1")
    assert "s1" in provider._sessions

    sdk.stop.side_effect = None
    await provider.stop_session("s1")
    assert "s1" not in provider._sessions


@pytest.mark.asyncio
async def test_browser_failed_stop_keeps_session_and_rebinds():
    provider = AgentCoreBrowserProvider()
    sdk = MagicMock(region="us-east-1")
    sdk.stop.side_effect = RuntimeError("ExpiredToken")
    provider._sessions["b1"] = sdk
    _as("AKIACALLER")

    with pytest.raises(RuntimeError):
        await provider.stop_session("b1")
    assert "b1" in provider._sessions
    assert _signing_key(sdk.data_plane_client) == "AKIACALLER"


def test_rebind_refuses_unknown_sdk_layout():
    """An SDK upgrade renaming the client attributes must fail loudly, not keep ambient clients."""
    import boto3

    from agentic_primitives_gateway.primitives._agentcore import rebind_agentcore_clients

    class _RenamedSdk:
        cp = object()

    with pytest.raises(RuntimeError, match="SDK layout changed"):
        rebind_agentcore_clients(
            _RenamedSdk(), boto3.Session(region_name="us-east-1"), control_plane="cp", data_plane="dp"
        )


@pytest.mark.asyncio
async def test_concurrent_calls_on_one_session_keep_their_own_identity():
    """Rebind + call are atomic per session; without that, B's rebind lands mid-way through A's call."""
    import asyncio
    import time

    provider = AgentCoreCodeInterpreterProvider(region="us-east-1")
    sdk = MagicMock(region="us-east-1")
    observed: list[tuple[str, str]] = []

    def _execute_code(code, language):
        before = _signing_key(sdk.data_plane_client)
        time.sleep(0.5)  # wide enough for the other request's rebind (session + client creation)
        observed.append((before, _signing_key(sdk.data_plane_client)))
        return {}

    sdk.execute_code.side_effect = _execute_code
    provider._sessions["s1"] = {"client": sdk, "created_at": "t", "language": "python"}

    async def _as_caller(key: str) -> None:
        _as(key)
        await provider.execute("s1", "x")

    await asyncio.gather(_as_caller("AKIAFIRST"), _as_caller("AKIASECOND"))
    assert sorted(observed) == [("AKIAFIRST", "AKIAFIRST"), ("AKIASECOND", "AKIASECOND")]


@pytest.mark.parametrize("kind", ["browser", "interpreter"])
@pytest.mark.asyncio
async def test_cancelled_worker_keeps_session_lock_until_its_sdk_call_finishes(kind: str):
    provider = AgentCoreBrowserProvider() if kind == "browser" else AgentCoreCodeInterpreterProvider()
    sdk = MagicMock(region="us-east-1")
    if kind == "browser":
        provider._sessions["s1"] = sdk
    else:
        provider._sessions["s1"] = {"client": sdk, "created_at": "t", "language": "python"}

    entered, release = threading.Event(), threading.Event()
    seen: list[tuple[str, str]] = []

    def first(client):
        before = _signing_key(client.data_plane_client)
        entered.set()
        if not release.wait(5):
            raise TimeoutError("SDK worker was never released")
        seen.append((before, _signing_key(client.data_plane_client)))

    def second(client):
        seen.append((_signing_key(client.data_plane_client), _signing_key(client.data_plane_client)))

    async def request(key: str, fn):
        _as(key)
        return await provider._call("s1", fn)

    first_request = asyncio.create_task(request("AKIAFIRST", first))
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait), 5)
        waiting_request = asyncio.create_task(request("AKIACANCELLED", second))
        await asyncio.sleep(0)
        waiting_request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting_request

        first_request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_request
        assert provider._locks["s1"].locked()

        last_request = asyncio.create_task(request("AKIASECOND", second))
        await asyncio.sleep(0)
        assert seen == []  # the cancelled worker still owns its SDK client
    finally:
        release.set()

    await asyncio.wait_for(last_request, 5)
    assert seen == [("AKIAFIRST", "AKIAFIRST"), ("AKIASECOND", "AKIASECOND")]


@pytest.mark.parametrize("kind", ["browser", "interpreter"])
@pytest.mark.asyncio
async def test_cancelled_stop_cleans_up_only_after_successful_sdk_completion(kind: str):
    provider = AgentCoreBrowserProvider() if kind == "browser" else AgentCoreCodeInterpreterProvider()
    sdk = MagicMock(region="us-east-1")
    if kind == "browser":
        provider._sessions["s1"] = sdk
        page, browser = MagicMock(), MagicMock()
        provider._pages["s1"] = page
        provider._browsers["s1"] = browser
    else:
        provider._sessions["s1"] = {"client": sdk, "created_at": "t", "language": "python"}

    entered, release = threading.Event(), threading.Event()

    def stop():
        entered.set()
        if not release.wait(5):
            raise TimeoutError("SDK stop was never released")

    sdk.stop.side_effect = stop
    _as("AKIACALLER")
    stopped = asyncio.create_task(provider.stop_session("s1"))
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait), 5)
        stopped.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopped
        assert provider._locks["s1"].locked()
        assert "s1" in provider._sessions
        if kind == "browser":
            assert provider._pages["s1"] is page
            assert provider._browsers["s1"] is browser
            page.close.assert_not_called()
            browser.close.assert_not_called()
    finally:
        release.set()

    async def stopped_remotely() -> None:
        while "s1" in provider._sessions:
            await asyncio.sleep(0)

    await asyncio.wait_for(stopped_remotely(), 5)
    if kind == "browser":
        assert "s1" not in provider._pages
        assert "s1" not in provider._browsers

        async def resources_closed() -> None:
            while not page.close.called or not browser.close.called:
                await asyncio.sleep(0)

        await asyncio.wait_for(resources_closed(), 5)
        page.close.assert_called_once()
        browser.close.assert_called_once()


@pytest.mark.parametrize("kind", ["browser", "interpreter"])
@pytest.mark.asyncio
async def test_current_credential_resolution_does_not_block_event_loop(kind: str):
    from unittest.mock import patch

    provider = AgentCoreBrowserProvider() if kind == "browser" else AgentCoreCodeInterpreterProvider()
    sdk = MagicMock(region="us-east-1")
    if kind == "browser":
        provider._sessions["s1"] = sdk
        module = "agentic_primitives_gateway.primitives.browser.agentcore.get_boto3_session"
    else:
        provider._sessions["s1"] = {"client": sdk, "created_at": "t", "language": "python"}
        module = "agentic_primitives_gateway.primitives.code_interpreter.agentcore.get_boto3_session"

    import boto3

    entered, release = threading.Event(), threading.Event()

    def blocked_lookup(*, default_region):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("Credential lookup was never released")
        return boto3.Session(aws_access_key_id="AKIACALLER", aws_secret_access_key="s", region_name=default_region)

    with patch(module, side_effect=blocked_lookup):
        running = asyncio.create_task(provider._call("s1", lambda c: c.get_session()))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait), 5)
        finally:
            release.set()
        await asyncio.wait_for(running, 5)


def test_rebind_preserves_sdk_client_config():
    """The code interpreter's data-plane client has a 300s read timeout; rebinding must keep it."""
    import boto3
    from bedrock_agentcore.tools import CodeInterpreter

    from agentic_primitives_gateway.primitives._agentcore import rebind_agentcore_clients

    sdk = CodeInterpreter(region="us-east-1", session=boto3.Session(region_name="us-east-1"))
    assert sdk.data_plane_client.meta.config.read_timeout == 300
    caller = boto3.Session(aws_access_key_id="AKIACALLER", aws_secret_access_key="s", region_name="us-east-1")
    rebind_agentcore_clients(sdk, caller, control_plane="control_plane_client", data_plane="data_plane_client")
    assert sdk.data_plane_client.meta.config.read_timeout == 300
    assert _signing_key(sdk.data_plane_client) == "AKIACALLER"


def test_live_view_signed_for_the_browser_sessions_region():
    import boto3

    from agentic_primitives_gateway.primitives.browser.agentcore import _bind_browser_session, _session_browser_client

    creds = {"aws_access_key_id": "AKIACALLER", "aws_secret_access_key": "s"}
    client = _session_browser_client(boto3.Session(region_name="us-west-2", **creds))
    client._identifier, client._session_id = "aws.browser.v1", "b1"
    # A later request whose credentials carry a different default region.
    _bind_browser_session(client, boto3.Session(region_name="us-east-1", **creds), region=client.region)
    url = client.generate_live_view_url(expires=60)
    assert "us-west-2" in url.split("?")[0]
    assert "%2Fus-west-2%2F" in url and "us-east-1" not in url


# ── Playwright interactions re-check the caller's current AWS credentials ──


def _browser_with_page(status: str = "READY") -> tuple[AgentCoreBrowserProvider, MagicMock, MagicMock]:
    from unittest.mock import AsyncMock

    provider = AgentCoreBrowserProvider()
    sdk = MagicMock(region="us-east-1")
    sdk.get_session.return_value = {"status": status}
    page = AsyncMock()
    page.evaluate.return_value = "text"
    provider._sessions["b1"] = sdk
    provider._pages["b1"] = page
    return provider, sdk, page


@pytest.mark.asyncio
async def test_interaction_refused_without_current_credentials_under_never():
    from agentic_primitives_gateway.config import settings
    from agentic_primitives_gateway.models.enums import ServerCredentialMode

    provider, _sdk, page = _browser_with_page()
    prev = settings.allow_server_credentials
    settings.allow_server_credentials = ServerCredentialMode.NEVER
    try:
        with pytest.raises(ValueError, match="No AWS credentials"):
            await provider.get_page_content("b1")
    finally:
        settings.allow_server_credentials = prev
    page.evaluate.assert_not_called()


@pytest.mark.asyncio
async def test_interaction_revalidates_same_key_and_revoked_credentials():
    provider, sdk, page = _browser_with_page()
    revoked = False

    def authorize():
        signer = sdk.data_plane_client._request_signer._credentials
        if signer.secret_key != "s" or revoked:
            raise RuntimeError("InvalidSignatureException")
        return {"status": "READY"}

    sdk.get_session.side_effect = authorize
    _as("AKIACALLER")
    await provider.get_page_content("b1")
    assert _signing_key(sdk.data_plane_client) == "AKIACALLER"

    # Access-key IDs are not proofs of authorization: a different secret
    # with the same ID must still make an AWS request and be rejected.
    set_aws_credentials(AWSCredentials(access_key_id="AKIACALLER", secret_access_key="wrong", region="us-west-2"))
    with pytest.raises(RuntimeError, match="InvalidSignatureException"):
        await provider.navigate("b1", "https://example.com")
    assert sdk.get_session.call_count == 2
    page.goto.assert_not_called()

    # Revocation can occur without the request's credential tuple changing.
    _as("AKIACALLER")
    revoked = True
    with pytest.raises(RuntimeError, match="InvalidSignatureException"):
        await provider.click("b1", "#x")
    assert sdk.get_session.call_count == 3
    page.click.assert_not_called()

    revoked = False
    _as("AKIAOTHER")
    await provider.get_page_content("b1")
    assert sdk.get_session.call_count == 4


@pytest.mark.asyncio
async def test_interaction_refused_when_session_not_ready():
    provider, _sdk, page = _browser_with_page(status="TERMINATED")
    _as("AKIACALLER")
    with pytest.raises(ValueError, match="not active"):
        await provider.click("b1", "#x")
    page.click.assert_not_called()
