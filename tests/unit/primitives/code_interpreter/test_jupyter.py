from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_primitives_gateway.primitives.code_interpreter.jupyter import (
    JupyterCodeInterpreterProvider,
)


def _mock_httpx_response(json_data=None, status_code=200):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    return resp


def _make_ws_message(msg_type, content, parent_msg_id="test-msg-id"):
    """Build a Jupyter wire-protocol response message."""
    return json.dumps(
        {
            "msg_type": msg_type,
            "parent_header": {"msg_id": parent_msg_id},
            "content": content,
        }
    )


class TestJupyterCodeInterpreterProvider:
    """Tests for the Jupyter code interpreter provider."""

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_start_session(self, mock_httpx, mock_ws):
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-abc"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider(base_url="http://localhost:8888")
        result = await provider.start_session()

        assert result["session_id"] == "kernel-abc"
        assert result["status"] == "active"
        assert result["language"] == "python"
        assert "created_at" in result
        mock_client.post.assert_called_once_with("/api/kernels", json={"name": "python3"})

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_start_session_custom_id(self, mock_httpx, mock_ws):
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-abc"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider(base_url="http://localhost:8888")
        result = await provider.start_session(session_id="my-session")

        assert result["session_id"] == "my-session"
        assert "my-session" in provider._sessions

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_stop_session(self, mock_httpx, mock_ws):
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.delete.return_value = _mock_httpx_response()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()
        await provider.stop_session("kernel-1")

        mock_ws_conn.close.assert_called_once()
        mock_client.delete.assert_called_once_with("/api/kernels/kernel-1")
        assert "kernel-1" not in provider._sessions

    @pytest.mark.asyncio
    async def test_stop_session_not_found(self):
        provider = JupyterCodeInterpreterProvider()
        # Should not raise
        await provider.stop_session("nonexistent")

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.uuid")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_execute(self, mock_httpx, mock_ws, mock_uuid):
        mock_uuid.uuid4.return_value = MagicMock(hex="test-msg-id")

        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        # WS will return stream + execute_result + execute_reply + status:idle
        ws_messages = [
            _make_ws_message("stream", {"name": "stdout", "text": "hello\n"}),
            _make_ws_message("execute_result", {"data": {"text/plain": "42"}}),
            _make_ws_message("execute_reply", {"status": "ok"}),
            _make_ws_message("status", {"execution_state": "idle"}),
        ]
        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws_conn.recv = AsyncMock(side_effect=ws_messages)
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()
        result = await provider.execute("kernel-1", "print('hello')")

        assert result["stdout"] == "hello\n"
        assert result["stderr"] == ""
        assert result["exit_code"] == 0
        assert result["result"] == "42"
        assert result["session_id"] == "kernel-1"

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.uuid")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_execute_error(self, mock_httpx, mock_ws, mock_uuid):
        mock_uuid.uuid4.return_value = MagicMock(hex="test-msg-id")

        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        ws_messages = [
            _make_ws_message("error", {"traceback": ["NameError: name 'x' is not defined"]}),
            _make_ws_message("execute_reply", {"status": "error"}),
            _make_ws_message("status", {"execution_state": "idle"}),
        ]
        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws_conn.recv = AsyncMock(side_effect=ws_messages)
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()
        result = await provider.execute("kernel-1", "print(x)")

        assert result["exit_code"] == 1
        assert "NameError" in result["stderr"]

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.uuid")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_execute_timeout(self, mock_httpx, mock_ws, mock_uuid):
        mock_uuid.uuid4.return_value = MagicMock(hex="test-msg-id")

        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws_conn.recv = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider(execution_timeout=1.0)
        await provider.start_session()
        result = await provider.execute("kernel-1", "import time; time.sleep(100)")

        assert result["exit_code"] == 1
        assert "timed out" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_execute_session_not_found(self):
        provider = JupyterCodeInterpreterProvider()
        with pytest.raises(ValueError, match="not found"):
            await provider.execute("missing", "code")

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.uuid")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_upload_file(self, mock_httpx, mock_ws, mock_uuid):
        mock_uuid.uuid4.return_value = MagicMock(hex="test-msg-id")

        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        # WS returns ok for the upload execute call
        ws_messages = [
            _make_ws_message("stream", {"name": "stdout", "text": "ok\n"}),
            _make_ws_message("status", {"execution_state": "idle"}),
        ]
        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws_conn.recv = AsyncMock(side_effect=ws_messages)
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()
        result = await provider.upload_file("kernel-1", "data.csv", b"col1,col2\n1,2")

        assert result["filename"] == "data.csv"
        assert result["size"] == len(b"col1,col2\n1,2")
        assert result["session_id"] == "kernel-1"

    @pytest.mark.asyncio
    async def test_upload_file_session_not_found(self):
        provider = JupyterCodeInterpreterProvider()
        with pytest.raises(ValueError, match="not found"):
            await provider.upload_file("missing", "file.txt", b"data")

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.uuid")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_download_file(self, mock_httpx, mock_ws, mock_uuid):
        import base64 as b64

        mock_uuid.uuid4.return_value = MagicMock(hex="test-msg-id")

        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        # WS returns base64-encoded file content via stdout
        encoded = b64.b64encode(b"binary content").decode("ascii")
        ws_messages = [
            _make_ws_message("stream", {"name": "stdout", "text": encoded + "\n"}),
            _make_ws_message("status", {"execution_state": "idle"}),
        ]
        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws_conn.recv = AsyncMock(side_effect=ws_messages)
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()
        result = await provider.download_file("kernel-1", "output.bin")

        assert result == b"binary content"

    @pytest.mark.asyncio
    async def test_download_file_session_not_found(self):
        provider = JupyterCodeInterpreterProvider()
        with pytest.raises(ValueError, match="not found"):
            await provider.download_file("missing", "file.txt")

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_list_sessions(self, mock_httpx, mock_ws):
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()

        sessions = await provider.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "kernel-1"
        assert sessions[0]["status"] == "active"

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_get_session(self, mock_httpx, mock_ws):
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()
        result = await provider.get_session("kernel-1")

        assert result["session_id"] == "kernel-1"
        assert result["status"] == "active"
        assert "created_at" in result

    @pytest.mark.asyncio
    async def test_get_session_not_found(self):
        provider = JupyterCodeInterpreterProvider()
        with pytest.raises(KeyError):
            await provider.get_session("nonexistent")

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.uuid")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_execution_history(self, mock_httpx, mock_ws, mock_uuid):
        mock_uuid.uuid4.return_value = MagicMock(hex="test-msg-id")

        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None

        def make_reply_messages():
            return [
                _make_ws_message("status", {"execution_state": "idle"}),
            ]

        mock_ws_conn.recv = AsyncMock(side_effect=make_reply_messages() + make_reply_messages())
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()
        await provider.execute("kernel-1", "print(1+1)")
        await provider.execute("kernel-1", "print(2+2)")

        history = await provider.get_execution_history("kernel-1")
        assert len(history) == 2
        assert history[0]["code"] == "print(1+1)"
        assert history[1]["code"] == "print(2+2)"

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_execution_history_empty(self, mock_httpx, mock_ws):
        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()
        history = await provider.get_execution_history("kernel-1")
        assert history == []

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.uuid")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_execution_history_respects_limit(self, mock_httpx, mock_ws, mock_uuid):
        mock_uuid.uuid4.return_value = MagicMock(hex="test-msg-id")

        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        replies = [_make_ws_message("status", {"execution_state": "idle"}) for _ in range(5)]
        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws_conn.recv = AsyncMock(side_effect=replies)
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()
        for i in range(5):
            await provider.execute("kernel-1", f"print({i})")

        history = await provider.get_execution_history("kernel-1", limit=2)
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_execution_history_session_not_found(self):
        provider = JupyterCodeInterpreterProvider()
        with pytest.raises(KeyError):
            await provider.get_execution_history("nonexistent")

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.uuid")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_stop_session_clears_history(self, mock_httpx, mock_ws, mock_uuid):
        mock_uuid.uuid4.return_value = MagicMock(hex="test-msg-id")

        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.delete.return_value = _mock_httpx_response()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        mock_ws_conn = AsyncMock()
        mock_ws_conn.close_code = None
        mock_ws_conn.recv = AsyncMock(return_value=_make_ws_message("status", {"execution_state": "idle"}))
        mock_ws.connect = AsyncMock(return_value=mock_ws_conn)

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()
        await provider.execute("kernel-1", "print(1)")
        await provider.stop_session("kernel-1")

        with pytest.raises(KeyError):
            await provider.get_execution_history("kernel-1")

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_healthcheck(self, mock_httpx):
        mock_client = AsyncMock()
        mock_client.get.return_value = _mock_httpx_response([])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        provider = JupyterCodeInterpreterProvider()
        result = await provider.healthcheck()
        assert result is True

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_healthcheck_failure(self, mock_httpx):
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("connection failed")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        provider = JupyterCodeInterpreterProvider()
        result = await provider.healthcheck()
        assert result is False

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.uuid")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.websockets")
    @patch("agentic_primitives_gateway.primitives.code_interpreter.jupyter.httpx")
    async def test_ws_reconnect(self, mock_httpx, mock_ws, mock_uuid):
        mock_uuid.uuid4.return_value = MagicMock(hex="test-msg-id")

        mock_client = AsyncMock()
        mock_client.post.return_value = _mock_httpx_response({"id": "kernel-1"})
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_httpx.AsyncClient.return_value = mock_client

        # First WS connection (returned by start_session)
        mock_ws_conn1 = AsyncMock()
        mock_ws_conn1.close_code = None
        mock_ws_conn1.recv = AsyncMock(return_value=_make_ws_message("status", {"execution_state": "idle"}))

        # Second WS connection (after reconnect)
        mock_ws_conn2 = AsyncMock()
        mock_ws_conn2.close_code = None
        mock_ws_conn2.recv = AsyncMock(return_value=_make_ws_message("status", {"execution_state": "idle"}))

        mock_ws.connect = AsyncMock(side_effect=[mock_ws_conn1, mock_ws_conn2])

        provider = JupyterCodeInterpreterProvider()
        await provider.start_session()

        # First execute works
        await provider.execute("kernel-1", "print(1)")

        # Simulate WS close
        mock_ws_conn1.close_code = 1000

        # Second execute should reconnect
        await provider.execute("kernel-1", "print(2)")

        # websockets.connect should have been called twice (initial + reconnect)
        assert mock_ws.connect.call_count == 2
