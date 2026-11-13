"""Shared Langfuse credential guards."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Verification never enters the shared executor. Acquire before submitting work
# so queued jobs cannot grow without bound, and keep the slot until the worker
# exits even if its caller is cancelled. The HTTP pool reuses connections.
_VERIFICATION_WORKERS = 4
_verification_slots = threading.BoundedSemaphore(_VERIFICATION_WORKERS)
_verification_executor = ThreadPoolExecutor(max_workers=_VERIFICATION_WORKERS, thread_name_prefix="langfuse-auth")
_verification_client = httpx.Client(timeout=httpx.Timeout(3.0, connect=1.0), follow_redirects=False)
# One in-flight first bind per public key. Entries are removed when their
# worker finishes; mismatched secrets cannot join a valid caller's check.
_pending_lock = threading.Lock()
_pending: dict[str, tuple[str, str, Future[Any]]] = {}


def require_langfuse_keys(creds: dict[str, Any]) -> tuple[str, str]:
    """Return ``(public_key, secret_key)`` or raise if either is missing.

    The Langfuse SDK silently substitutes ``LANGFUSE_PUBLIC_KEY`` /
    ``LANGFUSE_SECRET_KEY`` from the process environment for ``None``
    arguments.  ``get_service_credentials_or_defaults`` deliberately returns
    ``None`` for keys a caller didn't supply (``never`` / atomic ``fallback``),
    so passing those through would pair a caller's public key with the
    operator's secret.  Fail closed instead.
    """
    public_key, secret_key = creds.get("public_key"), creds.get("secret_key")
    if not (public_key and secret_key):
        raise ValueError(
            "Langfuse public_key and secret_key are both required. Pass them via X-Cred-Langfuse-* headers "
            "or apg.langfuse.* attributes, or enable allow_server_credentials: fallback."
        )
    return public_key, secret_key


def effective_langfuse_base_url(config_url: str | None) -> str:
    """Resolve the operator-selected target for SDK, REST and verification."""
    return (
        config_url
        or os.environ.get("LANGFUSE_BASE_URL")
        or os.environ.get("LANGFUSE_HOST")
        or "https://cloud.langfuse.com"
    )


def _verify_pair(public_key: str, secret_key: str, base_url: str) -> bool:
    """Verify before constructing an SDK client (and touching its public-key cache)."""
    try:
        resp = _verification_client.get(f"{base_url.rstrip('/')}/api/public/projects", auth=(public_key, secret_key))
    except httpx.HTTPError:
        return False
    return resp.status_code == 200


def assert_langfuse_binding(public_key: str, secret_key: str, base_url: str) -> bool:
    """Reject changed secrets/hosts without network; report whether a key is bound."""
    from langfuse._client.resource_manager import LangfuseResourceManager

    with LangfuseResourceManager._lock:
        bound = LangfuseResourceManager._instances.get(public_key)
        if bound is None:
            return False
        if not (
            hmac.compare_digest(str(bound.secret_key), secret_key)
            and str(bound.base_url).rstrip("/") == base_url.rstrip("/")
        ):
            raise ValueError("Langfuse credentials rejected; restart required to rotate a bound public key")
        return True


def _bound_client(langfuse_cls: Any, public_key: str, secret_key: str, base_url: str) -> Any | None:
    from langfuse._client.resource_manager import LangfuseResourceManager

    with LangfuseResourceManager._lock:
        if not assert_langfuse_binding(public_key, secret_key, base_url):
            return None
        return langfuse_cls(public_key=public_key, secret_key=secret_key, base_url=base_url)


def _bind_first(langfuse_cls: Any, public_key: str, secret_key: str, base_url: str) -> Any:
    from langfuse._client.resource_manager import LangfuseResourceManager

    # The key may have been bound while this worker waited for a slot.
    bound = _bound_client(langfuse_cls, public_key, secret_key, base_url)
    if bound is not None:
        return bound
    try:
        verified = _verify_pair(public_key, secret_key, base_url)
    except Exception:
        logger.debug("Langfuse credential verification failed", exc_info=True)
        verified = False
    if not verified:
        raise ValueError("Langfuse credentials rejected")
    # Another first-use request could have published this key during the HTTP
    # check. Check the binding again while holding the SDK's creation lock.
    with LangfuseResourceManager._lock:
        bound = _bound_client(langfuse_cls, public_key, secret_key, base_url)
        if bound is not None:
            return bound
        try:
            return langfuse_cls(public_key=public_key, secret_key=secret_key, base_url=base_url)
        except Exception:
            # Only our unpublished manager can be removed. Never tear down an
            # already returned manager: other requests may be using it.
            manager = LangfuseResourceManager._instances.pop(public_key, None)
            if manager is not None:
                try:
                    manager.shutdown()
                except Exception:
                    logger.debug("Langfuse failed manager shutdown failed", exc_info=True)
            raise


def _with_verification_slot(langfuse_cls: Any, public_key: str, secret_key: str, base_url: str) -> Any:
    try:
        return _bind_first(langfuse_cls, public_key, secret_key, base_url)
    finally:
        _verification_slots.release()


def bind_langfuse_sdk_client(langfuse_cls: Any, creds: dict[str, Any]) -> Any:
    """Bind a key once; changed secrets require a restart, not live rotation."""
    public_key, secret_key = require_langfuse_keys(creds)
    base_url = effective_langfuse_base_url(creds.get("base_url"))
    bound = _bound_client(langfuse_cls, public_key, secret_key, base_url)
    if bound is not None:
        return bound
    if not _verification_slots.acquire(blocking=False):
        raise ValueError("Langfuse credential verification busy")
    return _with_verification_slot(langfuse_cls, public_key, secret_key, base_url)


async def async_bind_langfuse_sdk_client(langfuse_cls: Any, creds: dict[str, Any]) -> Any:
    """Share first-use verification for one key pair without queuing a flood."""
    public_key, secret_key = require_langfuse_keys(creds)
    base_url = effective_langfuse_base_url(creds.get("base_url"))
    # Reject mismatched secrets before scheduling any worker or outbound request.
    if assert_langfuse_binding(public_key, secret_key, base_url):
        return _bound_client(langfuse_cls, public_key, secret_key, base_url)
    submitted = False
    with _pending_lock:
        pending = _pending.get(public_key)
        if pending is not None:
            if not (hmac.compare_digest(pending[0], secret_key) and pending[1] == base_url):
                raise ValueError("Langfuse credentials rejected")
            future = pending[2]
        else:
            if not _verification_slots.acquire(blocking=False):
                raise ValueError("Langfuse credential verification busy")
            try:
                future = _verification_executor.submit(
                    _with_verification_slot, langfuse_cls, public_key, secret_key, base_url
                )
            except BaseException:
                _verification_slots.release()
                raise
            submitted = True
            _pending[public_key] = (secret_key, base_url, future)

    if submitted:

        def forget(done: Future[Any]) -> None:
            with _pending_lock:
                if _pending.get(public_key, (None, None, None))[2] is done:
                    _pending.pop(public_key, None)

        future.add_done_callback(forget)
    # asyncio.wrap_future propagates cancellation to the worker future unless
    # shielded; a cancelled waiter must not cancel the shared verification.
    return await asyncio.shield(asyncio.wrap_future(future))
