"""Shared mixin for running synchronous functions in an executor."""

from __future__ import annotations

import asyncio
import contextvars
from functools import partial
from typing import Any


class SyncRunnerMixin:
    """Mixin that provides ``_run_sync`` for running blocking code in an executor.

    Providers that wrap synchronous client libraries (Langfuse, mem0, Keycloak,
    Okta, MSAL, boto3/AgentCore, etc.) should inherit from this mixin to avoid
    duplicating the executor boilerplate.
    """

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        # ``run_in_executor`` does not carry contextvars into the worker thread
        # (unlike ``asyncio.to_thread``).  Without the copy, any credential /
        # principal lookup inside ``func`` sees the defaults — i.e. no caller
        # credentials — and ``get_service_credentials_or_defaults`` /
        # ``get_boto3_session`` would silently fall back to operator creds.
        loop = asyncio.get_event_loop()
        ctx = contextvars.copy_context()
        return await loop.run_in_executor(None, partial(ctx.run, func, *args, **kwargs))
