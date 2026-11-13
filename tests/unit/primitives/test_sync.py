"""Request context survives executor-backed synchronous SDK operations."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.context import get_service_credentials_or_defaults, set_service_credentials
from agentic_primitives_gateway.models.enums import ServerCredentialMode
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin


@pytest.fixture
def caller_context() -> Iterator[None]:
    previous = settings.allow_server_credentials
    settings.allow_server_credentials = ServerCredentialMode.FALLBACK
    set_service_credentials({"example": {"client_id": "alice", "secret": "alice-secret"}})
    yield
    settings.allow_server_credentials = previous
    set_service_credentials({})


@pytest.mark.asyncio
async def test_executor_resolves_caller_credentials_instead_of_operator_defaults(caller_context: None) -> None:
    provider = SyncRunnerMixin()
    result = await provider._run_sync(
        get_service_credentials_or_defaults,
        "example",
        {"client_id": "operator", "secret": "operator-secret"},
    )
    assert result == {"client_id": "alice", "secret": "alice-secret"}
