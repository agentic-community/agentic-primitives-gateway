"""Shared mixin for running synchronous functions in an executor."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any


class SyncRunnerMixin:
    """Mixin that provides ``_run_sync`` for running blocking code in an executor.

    Providers that wrap synchronous client libraries (Langfuse, mem0, Keycloak,
    Okta, MSAL, boto3/AgentCore, etc.) should inherit from this mixin to avoid
    duplicating the executor boilerplate.
    """

    async def _run_sync(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, partial(func, *args, **kwargs))
