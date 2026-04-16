from __future__ import annotations

import pytest

from agentic_primitives_gateway.primitives.code_interpreter.noop import NoopCodeInterpreterProvider


class TestNoopCodeInterpreterExtended:
    """Test upload_file and download_file on the Noop code interpreter."""

    @pytest.fixture
    def provider(self):
        return NoopCodeInterpreterProvider()

    @pytest.mark.asyncio
    async def test_upload_file(self, provider):
        result = await provider.upload_file(
            session_id="sess-1",
            filename="data.csv",
            content=b"col1,col2\na,b",
        )
        assert result["filename"] == "data.csv"
        assert result["size"] == len(b"col1,col2\na,b")
        assert result["session_id"] == "sess-1"

    @pytest.mark.asyncio
    async def test_upload_file_empty_content(self, provider):
        result = await provider.upload_file(
            session_id="sess-2",
            filename="empty.txt",
            content=b"",
        )
        assert result["size"] == 0

    @pytest.mark.asyncio
    async def test_download_file_returns_empty_bytes(self, provider):
        result = await provider.download_file(session_id="sess-1", filename="out.txt")
        assert result == b""

    @pytest.mark.asyncio
    async def test_list_sessions_returns_empty(self, provider):
        result = await provider.list_sessions()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_sessions_with_status_filter(self, provider):
        result = await provider.list_sessions(status="active")
        assert result == []

    @pytest.mark.asyncio
    async def test_start_session_defaults(self, provider):
        result = await provider.start_session()
        assert result["session_id"] == "noop-session"
        assert result["status"] == "active"

    @pytest.mark.asyncio
    async def test_start_session_with_id(self, provider):
        result = await provider.start_session(session_id="custom-id")
        assert result["session_id"] == "custom-id"

    @pytest.mark.asyncio
    async def test_execute_returns_success(self, provider):
        result = await provider.execute(session_id="sess-1", code="print('hello')")
        assert result["exit_code"] == 0
        assert result["stdout"] == ""

    @pytest.mark.asyncio
    async def test_stop_session(self, provider):
        # Should not raise
        await provider.stop_session(session_id="sess-1")

    # ── Stateful session tracking ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_session(self, provider):
        await provider.start_session(session_id="s-1")
        result = await provider.get_session("s-1")
        assert result["session_id"] == "s-1"
        assert result["status"] == "active"
        assert "created_at" in result

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, provider):
        with pytest.raises(KeyError):
            await provider.get_session("nonexistent")

    @pytest.mark.asyncio
    async def test_list_sessions_returns_tracked(self, provider):
        await provider.start_session(session_id="s-a")
        await provider.start_session(session_id="s-b")
        sessions = await provider.list_sessions()
        session_ids = [s["session_id"] for s in sessions]
        assert "s-a" in session_ids
        assert "s-b" in session_ids

    @pytest.mark.asyncio
    async def test_stop_removes_from_list(self, provider):
        await provider.start_session(session_id="s-1")
        await provider.stop_session("s-1")
        sessions = await provider.list_sessions()
        assert all(s["session_id"] != "s-1" for s in sessions)

    @pytest.mark.asyncio
    async def test_execution_history_returns_empty(self, provider):
        await provider.start_session(session_id="s-1")
        history = await provider.get_execution_history("s-1")
        assert history == []

    @pytest.mark.asyncio
    async def test_execution_history_not_found(self, provider):
        with pytest.raises(KeyError):
            await provider.get_execution_history("nonexistent")
