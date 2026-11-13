"""Langfuse SDK binds each public key to a verified secret and operator target."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.context import set_service_credentials
from agentic_primitives_gateway.models.enums import ServerCredentialMode


@pytest.fixture(params=[ServerCredentialMode.NEVER, ServerCredentialMode.FALLBACK], ids=lambda m: m.value)
def mode(request: pytest.FixtureRequest) -> Iterator[ServerCredentialMode]:
    prev = settings.allow_server_credentials
    settings.allow_server_credentials = request.param
    yield request.param
    settings.allow_server_credentials = prev
    set_service_credentials({})


@pytest.mark.parametrize(
    ("module", "provider_cls", "build"),
    [
        ("observability", "LangfuseObservabilityProvider", "_resolve_client"),
        ("evaluations", "LangfuseEvaluationsProvider", "_resolve_sdk_client"),
        ("evaluations", "LangfuseEvaluationsProvider", "_resolve_rest_client"),
    ],
)
async def test_langfuse_partial_pair_never_picks_up_env_secret(mode, monkeypatch, module, provider_cls, build):
    """The Langfuse SDK reads ``LANGFUSE_SECRET_KEY`` when handed ``None``."""
    import importlib
    import inspect

    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "op-secret-from-env")
    mod = importlib.import_module(f"agentic_primitives_gateway.primitives.{module}.langfuse")
    provider = getattr(mod, provider_cls)(public_key="op-pk", base_url="https://lf.internal")
    set_service_credentials({"langfuse": {"public_key": "caller-pk"}})
    with (
        patch("langfuse.Langfuse") as sdk,
        patch(f"agentic_primitives_gateway.primitives.{module}.langfuse.Langfuse", sdk, create=True),
        patch("agentic_primitives_gateway.primitives.evaluations.langfuse.FernLangfuse") as rest,
        pytest.raises(ValueError, match="public_key and secret_key are both required"),
    ):
        result = getattr(provider, build)()
        if inspect.isawaitable(result):
            await result
    sdk.assert_not_called()
    rest.assert_not_called()


_VALID_SECRETS = {"real-secret"}


class _FakeLangfuse:
    """Mirrors Langfuse v3: one resource manager per public key, reused whatever secret is passed."""

    auth_error: Exception | None = None

    def __init__(self, *, public_key: str, secret_key: str, base_url: str | None) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from langfuse._client.resource_manager import LangfuseResourceManager

        LangfuseResourceManager._instances.setdefault(
            public_key,
            SimpleNamespace(
                secret_key=secret_key, base_url=base_url or "https://cloud.langfuse.com", shutdown=MagicMock()
            ),
        )
        self._rm = LangfuseResourceManager._instances[public_key]

    def auth_check(self) -> bool:
        if _FakeLangfuse.auth_error is not None:
            raise _FakeLangfuse.auth_error
        return self._rm.secret_key in _VALID_SECRETS


@pytest.fixture
def _clean_langfuse_instances() -> Iterator[None]:
    from langfuse._client.resource_manager import LangfuseResourceManager

    saved = dict(LangfuseResourceManager._instances)
    LangfuseResourceManager._instances.clear()
    with patch(
        "agentic_primitives_gateway.primitives._langfuse._verify_pair",
        side_effect=lambda _pk, sk, _url: sk in _VALID_SECRETS,
    ):
        yield
    _FakeLangfuse.auth_error = None
    _VALID_SECRETS.clear()
    _VALID_SECRETS.add("real-secret")
    LangfuseResourceManager._instances.clear()
    LangfuseResourceManager._instances.update(saved)


def _bind(secret: str, pk: str = "pk-shared"):
    from agentic_primitives_gateway.primitives._langfuse import bind_langfuse_sdk_client

    return bind_langfuse_sdk_client(_FakeLangfuse, {"public_key": pk, "secret_key": secret})


def _bound(pk: str = "pk-shared"):
    from langfuse._client.resource_manager import LangfuseResourceManager

    return LangfuseResourceManager._instances.get(pk)


@pytest.mark.usefixtures("_clean_langfuse_instances")
def test_langfuse_bound_public_key_rejects_other_secret():
    _bind("real-secret")
    with pytest.raises(ValueError, match="rejected"):
        _bind("guessed")
    assert _bound().secret_key == "real-secret"


@pytest.mark.usefixtures("_clean_langfuse_instances")
def test_langfuse_invalid_first_secret_does_not_bind_public_key():
    with pytest.raises(ValueError, match="rejected"):
        _bind("guessed")
    assert _bound() is None
    _bind("real-secret")


@pytest.mark.usefixtures("_clean_langfuse_instances")
def test_langfuse_verified_first_bind_does_not_repeat_auth_check():
    # Verification uses /projects before SDK construction; SDK auth_check
    # could be slow and must not double the first-use network round-trip.
    _FakeLangfuse.auth_error = RuntimeError("auth_check must not run")
    client = _bind("real-secret")
    assert client._rm is _bound()


@pytest.mark.usefixtures("_clean_langfuse_instances")
def test_langfuse_rejects_different_target_without_basic_request():
    from agentic_primitives_gateway.primitives._langfuse import bind_langfuse_sdk_client

    _bind("real-secret")
    with patch("agentic_primitives_gateway.primitives._langfuse._verify_pair") as verify:
        with pytest.raises(ValueError, match="restart required"):
            bind_langfuse_sdk_client(
                _FakeLangfuse,
                {"public_key": "pk-shared", "secret_key": "real-secret", "base_url": "https://wrong.test"},
            )
        verify.assert_not_called()


@pytest.mark.usefixtures("_clean_langfuse_instances")
def test_langfuse_first_bind_sends_basic_only_to_selected_host():
    from agentic_primitives_gateway.primitives._langfuse import bind_langfuse_sdk_client

    with patch("agentic_primitives_gateway.primitives._langfuse._verify_pair", return_value=True) as verify:
        bind_langfuse_sdk_client(
            _FakeLangfuse,
            {"public_key": "pk-shared", "secret_key": "real-secret", "base_url": "https://operator.test"},
        )
        verify.assert_called_once_with("pk-shared", "real-secret", "https://operator.test")


def test_langfuse_host_resolution_prefers_config_then_base_env_then_host_env():
    from agentic_primitives_gateway.primitives._langfuse import effective_langfuse_base_url

    with patch.dict(
        "os.environ",
        {"LANGFUSE_BASE_URL": "https://base.test", "LANGFUSE_HOST": "https://host.test"},
        clear=True,
    ):
        assert effective_langfuse_base_url("https://configured.test") == "https://configured.test"
        assert effective_langfuse_base_url(None) == "https://base.test"
    with patch.dict("os.environ", {"LANGFUSE_HOST": "https://host.test"}, clear=True):
        assert effective_langfuse_base_url(None) == "https://host.test"


def test_langfuse_verification_uses_selected_host_and_basic_pair():
    from agentic_primitives_gateway.primitives._langfuse import _verify_pair

    with patch("agentic_primitives_gateway.primitives._langfuse._verification_client.get") as get:
        get.return_value.status_code = 200
        assert _verify_pair("pk", "sk", "https://operator.test/") is True
        get.assert_called_once_with("https://operator.test/api/public/projects", auth=("pk", "sk"))


@pytest.mark.usefixtures("_clean_langfuse_instances")
def test_langfuse_failed_construction_discards_unpublished_manager():
    class _BrokenFirst(_FakeLangfuse):
        def __init__(self, *, public_key: str, secret_key: str, base_url: str | None) -> None:
            super().__init__(public_key=public_key, secret_key=secret_key, base_url=base_url)
            raise RuntimeError("SDK initialization failed")

    from agentic_primitives_gateway.primitives._langfuse import bind_langfuse_sdk_client

    with pytest.raises(RuntimeError, match="SDK initialization failed"):
        bind_langfuse_sdk_client(_BrokenFirst, {"public_key": "pk-shared", "secret_key": "real-secret"})
    assert _bound() is None
    _bind("real-secret")


@pytest.mark.usefixtures("_clean_langfuse_instances")
def test_langfuse_rotation_requires_restart_without_disrupting_in_flight_manager():
    _bind("real-secret")
    old = _bound()
    _VALID_SECRETS.clear()
    _VALID_SECRETS.add("rotated")
    with patch("agentic_primitives_gateway.primitives._langfuse._verify_pair") as verify:
        with pytest.raises(ValueError, match="restart required"):
            _bind("rotated")
        verify.assert_not_called()
    old.shutdown.assert_not_called()
    assert _bound() is old
    assert _bind("real-secret")._rm is old


@pytest.mark.usefixtures("_clean_langfuse_instances")
def test_langfuse_slow_first_bind_does_not_block_other_keys():
    import threading

    from agentic_primitives_gateway.primitives._langfuse import bind_langfuse_sdk_client

    entered = threading.Event()
    release = threading.Event()

    def slow_check(pk: str, _sk: str, _url: str) -> bool:
        if pk == "pk-slow":
            entered.set()
            release.wait(5)
        return True

    with patch("agentic_primitives_gateway.primitives._langfuse._verify_pair", side_effect=slow_check):
        slow = threading.Thread(
            target=bind_langfuse_sdk_client,
            args=(_FakeLangfuse, {"public_key": "pk-slow", "secret_key": "real-secret"}),
        )
        slow.start()
        try:
            assert entered.wait(2)
            done = threading.Event()
            fast = threading.Thread(target=lambda: (_bind("real-secret", pk="pk-fast"), done.set()))
            fast.start()
            assert done.wait(2), "binding another key waited for the slow network check"
            fast.join(5)
        finally:
            release.set()
            slow.join(5)


@pytest.mark.usefixtures("_clean_langfuse_instances")
@pytest.mark.asyncio
async def test_langfuse_unbound_flood_does_not_queue_on_default_executor():
    import asyncio
    import threading

    from agentic_primitives_gateway.primitives._langfuse import (
        _VERIFICATION_WORKERS,
        async_bind_langfuse_sdk_client,
    )

    entered = threading.Barrier(_VERIFICATION_WORKERS + 1)
    release = threading.Event()

    def slow_check(_pk: str, _sk: str, _url: str) -> bool:
        entered.wait(timeout=3)
        release.wait(timeout=3)
        return True

    with patch("agentic_primitives_gateway.primitives._langfuse._verify_pair", side_effect=slow_check):
        pending = [
            asyncio.create_task(
                async_bind_langfuse_sdk_client(
                    _FakeLangfuse, {"public_key": f"pk-flood-{i}", "secret_key": "real-secret"}
                )
            )
            for i in range(_VERIFICATION_WORKERS)
        ]
        try:
            await asyncio.to_thread(entered.wait, 3)
            with pytest.raises(ValueError, match="verification busy"):
                await async_bind_langfuse_sdk_client(
                    _FakeLangfuse, {"public_key": "pk-extra", "secret_key": "real-secret"}
                )
            pending[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending[0]
            with pytest.raises(ValueError, match="verification busy"):
                await async_bind_langfuse_sdk_client(
                    _FakeLangfuse, {"public_key": "pk-extra", "secret_key": "real-secret"}
                )
        finally:
            release.set()
            await asyncio.gather(*pending, return_exceptions=True)
        assert _bound("pk-flood-0") is not None


@pytest.mark.usefixtures("_clean_langfuse_instances")
@pytest.mark.asyncio
async def test_langfuse_same_key_first_use_burst_shares_one_check():
    import asyncio
    import threading

    from agentic_primitives_gateway.primitives._langfuse import _VERIFICATION_WORKERS, async_bind_langfuse_sdk_client

    started = threading.Event()
    release = threading.Event()
    checks: list[str] = []

    def slow_check(pk: str, _sk: str, _url: str) -> bool:
        checks.append(pk)
        started.set()
        return release.wait(timeout=5)

    creds = {"public_key": "pk-burst", "secret_key": "real-secret"}
    with patch("agentic_primitives_gateway.primitives._langfuse._verify_pair", side_effect=slow_check):
        first = asyncio.create_task(async_bind_langfuse_sdk_client(_FakeLangfuse, creds))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            rest = [
                asyncio.create_task(async_bind_langfuse_sdk_client(_FakeLangfuse, creds))
                for _ in range(_VERIFICATION_WORKERS + 2)
            ]
            await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        finally:
            release.set()
        results = await asyncio.gather(*rest)
    assert len(checks) == 1
    assert all(result._rm is _bound("pk-burst") for result in results)


@pytest.mark.usefixtures("_clean_langfuse_instances")
@pytest.mark.asyncio
async def test_langfuse_async_rejects_bound_secret_without_http():
    from agentic_primitives_gateway.primitives._langfuse import async_bind_langfuse_sdk_client

    _bind("real-secret")
    with patch("agentic_primitives_gateway.primitives._langfuse._verify_pair") as verify:
        with pytest.raises(ValueError, match="restart required"):
            await async_bind_langfuse_sdk_client(_FakeLangfuse, {"public_key": "pk-shared", "secret_key": "wrong"})
        verify.assert_not_called()
