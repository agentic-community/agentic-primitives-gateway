"""Small targeted tests to close remaining coverage gaps."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agentic_primitives_gateway.agents.store import FileAgentStore
from agentic_primitives_gateway.primitives.browser.noop import NoopBrowserProvider
from agentic_primitives_gateway.routes._helpers import handle_provider_errors

# ── _helpers.py line 37: KeyError re-raised when not_found is None ───


class TestHandleProviderErrorsKeyErrorReraise:
    async def test_keyerror_reraised_when_not_found_is_none(self) -> None:
        @handle_provider_errors("not implemented")
        async def raises_key_error():
            raise KeyError("missing")

        with pytest.raises(KeyError, match="missing"):
            await raises_key_error()


# ── browser/noop.py lines 32-33 ─────────────────────────────────────


class TestNoopBrowserExtended:
    async def test_get_session(self) -> None:
        p = NoopBrowserProvider()
        result = await p.get_session("s1")
        assert result["session_id"] == "s1"

    async def test_list_sessions(self) -> None:
        p = NoopBrowserProvider()
        result = await p.list_sessions()
        assert result == []


# ── store.py lines 52-53: load failure ───────────────────────────────


class TestFileAgentStoreLoadFailure:
    def test_load_corrupted_file(self, tmp_path) -> None:
        path = tmp_path / "agents.json"
        path.write_text("not valid json!!!")
        store = FileAgentStore(path=str(path))
        # Should not raise, just log and continue with empty store
        assert store._agents == {}

    async def test_update_nonexistent_raises(self, tmp_path) -> None:
        store = FileAgentStore(path=str(tmp_path / "agents.json"))
        with pytest.raises(KeyError, match="Agent not found"):
            await store.update("nonexistent", {"description": "x"})


# ── routes/tools.py lines 53, 86, 93 ────────────────────────────────


class TestToolsRouteExtended:
    def test_get_tool_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tools/unknown-tool")
        assert resp.status_code == 501

    def test_delete_tool_501(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/tools/unknown-tool")
        assert resp.status_code == 501

    def test_list_servers_501(self, client: TestClient) -> None:
        resp = client.get("/api/v1/tools/servers")
        assert resp.status_code == 501
