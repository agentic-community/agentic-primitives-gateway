from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentic_primitives_gateway.auth.models import ANONYMOUS_PRINCIPAL
from agentic_primitives_gateway.auth.noop import NoopAuthBackend


class TestNoopAuthBackend:
    @pytest.mark.asyncio
    async def test_always_returns_anonymous(self):
        backend = NoopAuthBackend()
        request = MagicMock()
        principal = await backend.authenticate(request)
        assert principal is ANONYMOUS_PRINCIPAL

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        backend = NoopAuthBackend()
        await backend.close()  # should not raise
