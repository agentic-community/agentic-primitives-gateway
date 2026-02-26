from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.context import AWSCredentials, set_aws_credentials
from agentic_primitives_gateway.primitives.code_interpreter.agentcore import (
    AgentCoreCodeInterpreterProvider,
)


@patch("agentic_primitives_gateway.primitives.code_interpreter.agentcore.get_boto3_session")
@patch("agentic_primitives_gateway.primitives.code_interpreter.agentcore.AgentCoreCodeInterpreter")
class TestAgentCoreCodeInterpreterProvider:
    """Tests for the AgentCore code interpreter provider."""

    @pytest.mark.asyncio
    async def test_start_session_string_result(self, mock_ci_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "sess-abc"
        mock_ci_cls.return_value = mock_client

        provider = AgentCoreCodeInterpreterProvider(region="us-east-1")
        result = await provider.start_session(session_id="my-sess")

        assert result["session_id"] == "sess-abc"
        assert result["status"] == "active"
        assert result["language"] == "python"

    @pytest.mark.asyncio
    async def test_start_session_dict_result(self, mock_ci_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = {"sessionId": "dict-sess"}
        mock_ci_cls.return_value = mock_client

        provider = AgentCoreCodeInterpreterProvider()
        result = await provider.start_session()

        assert result["session_id"] == "dict-sess"

    @pytest.mark.asyncio
    async def test_stop_session(self, mock_ci_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "s1"
        mock_ci_cls.return_value = mock_client

        provider = AgentCoreCodeInterpreterProvider()
        await provider.start_session()
        await provider.stop_session("s1")

        mock_client.stop.assert_called_once()
        assert "s1" not in provider._sessions

    @pytest.mark.asyncio
    async def test_stop_session_not_found(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider()
        # Should not raise
        await provider.stop_session("nonexistent")

    @pytest.mark.asyncio
    async def test_execute_success(self, mock_ci_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "s1"
        mock_client.execute_code.return_value = {
            "stdout": "hello\n",
            "stderr": "",
            "exitCode": 0,
            "result": "hello",
        }
        mock_ci_cls.return_value = mock_client

        provider = AgentCoreCodeInterpreterProvider()
        await provider.start_session()
        result = await provider.execute("s1", "print('hello')")

        assert result["stdout"] == "hello\n"
        assert result["exit_code"] == 0
        assert result["result"] == "hello"

    @pytest.mark.asyncio
    async def test_execute_session_not_found(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider()
        with pytest.raises(ValueError, match="not found"):
            await provider.execute("missing", "code")

    @pytest.mark.asyncio
    async def test_upload_file(self, mock_ci_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "s1"
        mock_ci_cls.return_value = mock_client

        provider = AgentCoreCodeInterpreterProvider()
        await provider.start_session()
        result = await provider.upload_file("s1", "data.csv", b"col1,col2\n1,2")

        assert result["filename"] == "data.csv"
        assert result["size"] == len(b"col1,col2\n1,2")
        mock_client.upload_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_file_session_not_found(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider()
        with pytest.raises(ValueError, match="not found"):
            await provider.upload_file("missing", "file.txt", b"data")

    @pytest.mark.asyncio
    async def test_download_file(self, mock_ci_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "s1"

        # download_file writes to a destination directory
        def fake_download(file_name, destination):
            filepath = os.path.join(destination, file_name)
            with open(filepath, "wb") as f:
                f.write(b"downloaded content")

        mock_client.download_file.side_effect = fake_download
        mock_ci_cls.return_value = mock_client

        provider = AgentCoreCodeInterpreterProvider()
        await provider.start_session()
        result = await provider.download_file("s1", "output.txt")

        assert result == b"downloaded content"

    @pytest.mark.asyncio
    async def test_download_file_session_not_found(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider()
        with pytest.raises(ValueError, match="not found"):
            await provider.download_file("missing", "file.txt")

    @pytest.mark.asyncio
    async def test_list_sessions(self, mock_ci_cls, mock_get_session):
        mock_get_session.return_value = MagicMock(region_name="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "s1"
        mock_ci_cls.return_value = mock_client

        provider = AgentCoreCodeInterpreterProvider()
        await provider.start_session()

        sessions = await provider.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "s1"
        assert sessions[0]["status"] == "active"

    @pytest.mark.asyncio
    async def test_healthcheck(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider()
        assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_get_region_from_context(self, mock_ci_cls, mock_get_session):
        """_get_region prefers region from request context credentials."""
        provider = AgentCoreCodeInterpreterProvider(region="us-east-1")
        set_aws_credentials(AWSCredentials(access_key_id="AK", secret_access_key="SK", region="eu-west-1"))
        assert provider._get_region() == "eu-west-1"

    @pytest.mark.asyncio
    async def test_get_region_fallback_to_config(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider(region="ap-south-1")
        set_aws_credentials(None)
        assert provider._get_region() == "ap-south-1"

    @pytest.mark.asyncio
    async def test_get_region_creds_no_region(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider(region="us-west-2")
        set_aws_credentials(AWSCredentials(access_key_id="AK", secret_access_key="SK"))
        assert provider._get_region() == "us-west-2"

    # ── New methods ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_get_session(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider(region="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "session-1"
        mock_ci_cls.return_value = mock_client
        mock_get_session.return_value = MagicMock(region_name="us-east-1")

        await provider.start_session(session_id="session-1")
        result = await provider.get_session("session-1")

        assert result["session_id"] == "session-1"
        assert result["status"] == "active"
        assert "created_at" in result

    @pytest.mark.asyncio
    async def test_get_session_not_found(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider(region="us-east-1")
        with pytest.raises(KeyError):
            await provider.get_session("nonexistent")

    @pytest.mark.asyncio
    async def test_execution_history(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider(region="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "session-1"
        mock_client.execute_code.return_value = {"stdout": "2", "stderr": "", "exitCode": 0}
        mock_ci_cls.return_value = mock_client
        mock_get_session.return_value = MagicMock(region_name="us-east-1")

        await provider.start_session(session_id="session-1")
        await provider.execute("session-1", "print(1+1)")
        await provider.execute("session-1", "print(2+2)")

        history = await provider.get_execution_history("session-1")
        assert len(history) == 2
        assert history[0]["code"] == "print(1+1)"
        assert history[1]["code"] == "print(2+2)"

    @pytest.mark.asyncio
    async def test_execution_history_empty(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider(region="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "session-1"
        mock_ci_cls.return_value = mock_client
        mock_get_session.return_value = MagicMock(region_name="us-east-1")

        await provider.start_session(session_id="session-1")
        history = await provider.get_execution_history("session-1")
        assert history == []

    @pytest.mark.asyncio
    async def test_execution_history_respects_limit(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider(region="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "session-1"
        mock_client.execute_code.return_value = {"stdout": "", "stderr": "", "exitCode": 0}
        mock_ci_cls.return_value = mock_client
        mock_get_session.return_value = MagicMock(region_name="us-east-1")

        await provider.start_session(session_id="session-1")
        for i in range(5):
            await provider.execute("session-1", f"print({i})")

        history = await provider.get_execution_history("session-1", limit=2)
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_stop_session_clears_history(self, mock_ci_cls, mock_get_session):
        provider = AgentCoreCodeInterpreterProvider(region="us-east-1")
        mock_client = MagicMock()
        mock_client.start.return_value = "session-1"
        mock_client.execute_code.return_value = {"stdout": "", "stderr": "", "exitCode": 0}
        mock_ci_cls.return_value = mock_client
        mock_get_session.return_value = MagicMock(region_name="us-east-1")

        await provider.start_session(session_id="session-1")
        await provider.execute("session-1", "print(1)")
        await provider.stop_session("session-1")

        with pytest.raises(KeyError):
            await provider.get_execution_history("session-1")
