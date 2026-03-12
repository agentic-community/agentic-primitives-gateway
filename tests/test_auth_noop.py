from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentic_primitives_gateway.auth.models import NOOP_PRINCIPAL
from agentic_primitives_gateway.auth.noop import NoopAuthBackend


class TestNoopAuthBackend:
    @pytest.mark.asyncio
    async def test_returns_noop_admin_principal(self):
        backend = NoopAuthBackend()
        request = MagicMock()
        principal = await backend.authenticate(request)
        assert principal is NOOP_PRINCIPAL
        assert principal.id == "noop"
        assert principal.is_admin is True
        assert principal.is_anonymous is False

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        backend = NoopAuthBackend()
        await backend.close()  # should not raise
