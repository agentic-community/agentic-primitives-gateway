"""System tests for the AgentCore code interpreter primitive.

Full stack: AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreCodeInterpreterProvider → (mocked) AgentCoreCodeInterpreter SDK.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError

# ── Session management ────────────────────────────────────────────────


class TestStartSession:
    async def test_start_session(self, client: AgenticPlatformClient, mock_code_interpreter: MagicMock) -> None:
        mock_code_interpreter.start.return_value = "ci-sess-1"

        result = await client.start_code_session()

        assert result["session_id"] == "ci-sess-1"
        assert result["status"] == "active"

    async def test_start_session_with_id(self, client: AgenticPlatformClient, mock_code_interpreter: MagicMock) -> None:
        mock_code_interpreter.start.return_value = "my-session"

        result = await client.start_code_session(session_id="my-session")

        assert result["session_id"] == "my-session"


class TestStopSession:
    async def test_stop_session(self, client: AgenticPlatformClient, mock_code_interpreter: MagicMock) -> None:
        mock_code_interpreter.start.return_value = "ci-sess-1"
        await client.start_code_session()

        mock_code_interpreter.stop.return_value = None
        await client.stop_code_session("ci-sess-1")


# ── Code execution ────────────────────────────────────────────────────


class TestExecuteCode:
    async def test_execute_code(self, client: AgenticPlatformClient, mock_code_interpreter: MagicMock) -> None:
        mock_code_interpreter.start.return_value = "ci-sess-1"
        await client.start_code_session()

        mock_code_interpreter.execute_code.return_value = {
            "stdout": "hello\n",
            "stderr": "",
            "exitCode": 0,
            "result": None,
        }

        result = await client.execute_code("ci-sess-1", "print('hello')")

        assert result["session_id"] == "ci-sess-1"
        assert result["stdout"] == "hello\n"
        assert result["exit_code"] == 0

    async def test_execute_session_not_found(
        self, client: AgenticPlatformClient, mock_code_interpreter: MagicMock
    ) -> None:
        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.execute_code("nonexistent", "print(1)")
        assert exc_info.value.status_code == 404


# ── File upload / download ────────────────────────────────────────────


class TestUploadFile:
    async def test_upload_file(self, client: AgenticPlatformClient, mock_code_interpreter: MagicMock) -> None:
        mock_code_interpreter.start.return_value = "ci-sess-1"
        await client.start_code_session()

        mock_code_interpreter.upload_file.return_value = None

        result = await client.upload_file("ci-sess-1", "data.csv", b"col1,col2\n1,2")

        assert result["filename"] == "data.csv"
        assert result["session_id"] == "ci-sess-1"


class TestDownloadFile:
    async def test_download_file(self, client: AgenticPlatformClient, mock_code_interpreter: MagicMock) -> None:
        mock_code_interpreter.start.return_value = "ci-sess-1"
        await client.start_code_session()

        def fake_download(file_name: str, destination: str) -> None:
            filepath = os.path.join(destination, file_name)
            with open(filepath, "wb") as f:
                f.write(b"output data")

        mock_code_interpreter.download_file.side_effect = fake_download

        content = await client.download_file("ci-sess-1", "output.txt")

        assert content == b"output data"


# ── Session listing / retrieval ───────────────────────────────────────


class TestListSessions:
    async def test_list_sessions(self, client: AgenticPlatformClient, mock_code_interpreter: MagicMock) -> None:
        mock_code_interpreter.start.return_value = "ci-sess-1"
        await client.start_code_session()

        result = await client.list_code_sessions()

        assert "sessions" in result
        assert any(s["session_id"] == "ci-sess-1" for s in result["sessions"])


class TestGetSession:
    async def test_get_session(self, client: AgenticPlatformClient, mock_code_interpreter: MagicMock) -> None:
        mock_code_interpreter.start.return_value = "ci-sess-1"
        await client.start_code_session()

        result = await client.get_code_session("ci-sess-1")

        assert result["session_id"] == "ci-sess-1"
        assert result["status"] == "active"

    async def test_get_session_not_found(self, client: AgenticPlatformClient, mock_code_interpreter: MagicMock) -> None:
        with pytest.raises(AgenticPlatformError) as exc_info:
            await client.get_code_session("missing")
        assert exc_info.value.status_code == 404


# ── Execution history ─────────────────────────────────────────────────


class TestExecutionHistory:
    async def test_execution_history(self, client: AgenticPlatformClient, mock_code_interpreter: MagicMock) -> None:
        mock_code_interpreter.start.return_value = "ci-sess-1"
        await client.start_code_session()

        mock_code_interpreter.execute_code.return_value = {
            "stdout": "2\n",
            "stderr": "",
            "exitCode": 0,
            "result": None,
        }
        await client.execute_code("ci-sess-1", "print(1+1)")

        result = await client.get_execution_history("ci-sess-1")

        assert "entries" in result
        assert len(result["entries"]) == 1
        assert result["entries"][0]["code"] == "print(1+1)"
