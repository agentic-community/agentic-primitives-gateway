from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets

from agentic_primitives_gateway.models.enums import CodeLanguage, SessionStatus
from agentic_primitives_gateway.primitives.code_interpreter.base import CodeInterpreterProvider

logger = logging.getLogger(__name__)


class JupyterCodeInterpreterProvider(CodeInterpreterProvider):
    """Code interpreter backed by Jupyter Server or Enterprise Gateway.

    Uses the Jupyter REST API for kernel lifecycle and file I/O, and
    WebSocket (Jupyter wire protocol) for code execution.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.code_interpreter.jupyter.JupyterCodeInterpreterProvider
        config:
          base_url: "http://localhost:8888"
          token: ""
          kernel_name: "python3"
          execution_timeout: 30.0
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8888",
        token: str = "",
        kernel_name: str = "python3",
        execution_timeout: float = 30.0,
        file_root: str = "/tmp",
        **kwargs: Any,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._kernel_name = kernel_name
        self._execution_timeout = execution_timeout
        self._file_root = file_root
        # session_id -> {kernel_id, ws, created_at, language}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}
        logger.info(
            "Jupyter code interpreter provider initialized (base_url=%s, kernel=%s)",
            self._base_url,
            self._kernel_name,
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _http_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"token {self._token}"
        return headers

    def _ws_url(self, kernel_id: str) -> str:
        """Build WebSocket URL for kernel channels."""
        base = self._base_url.replace("http://", "ws://").replace("https://", "wss://")
        url = f"{base}/api/kernels/{kernel_id}/channels"
        if self._token:
            url += f"?token={self._token}"
        return url

    async def _open_ws(self, kernel_id: str) -> websockets.ClientConnection:
        return await websockets.connect(self._ws_url(kernel_id))

    async def _ensure_ws(self, session_id: str) -> websockets.ClientConnection:
        """Return existing WS or reconnect if closed."""
        session = self._sessions[session_id]
        ws: websockets.ClientConnection = session["ws"]
        if ws.close_code is not None:
            logger.info("WebSocket closed for session %s, reconnecting", session_id)
            ws = await self._open_ws(session["kernel_id"])
            session["ws"] = ws
        return ws

    @staticmethod
    def _make_execute_request(code: str) -> tuple[str, dict[str, Any]]:
        """Build a Jupyter wire-protocol execute_request message."""
        msg_id = uuid.uuid4().hex
        msg = {
            "header": {
                "msg_id": msg_id,
                "msg_type": "execute_request",
                "username": "",
                "session": "",
                "version": "5.3",
            },
            "parent_header": {},
            "metadata": {},
            "content": {
                "code": code,
                "silent": False,
                "store_history": True,
                "user_expressions": {},
                "allow_stdin": False,
                "stop_on_error": True,
            },
            "buffers": [],
            "channel": "shell",
        }
        return msg_id, msg

    # ── ABC implementation ─────────────────────────────────────────────

    async def start_session(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self._base_url, headers=self._http_headers()) as client:
            resp = await client.post("/api/kernels", json={"name": self._kernel_name})
            resp.raise_for_status()
            kernel = resp.json()

        kernel_id: str = kernel["id"]
        sid = session_id or kernel_id
        ws = await self._open_ws(kernel_id)

        self._sessions[sid] = {
            "kernel_id": kernel_id,
            "ws": ws,
            "created_at": datetime.now(UTC).isoformat(),
            "language": CodeLanguage.PYTHON,
        }
        self._history[sid] = []
        return {
            "session_id": sid,
            "status": SessionStatus.ACTIVE,
            "language": CodeLanguage.PYTHON,
            "created_at": self._sessions[sid]["created_at"],
        }

    async def stop_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        self._history.pop(session_id, None)
        if not session:
            return

        ws: websockets.ClientConnection = session["ws"]
        await ws.close()

        async with httpx.AsyncClient(base_url=self._base_url, headers=self._http_headers()) as client:
            resp = await client.delete(f"/api/kernels/{session['kernel_id']}")
            resp.raise_for_status()

    async def execute(
        self,
        session_id: str,
        code: str,
        language: str = CodeLanguage.PYTHON,
    ) -> dict[str, Any]:
        if session_id not in self._sessions:
            raise ValueError(f"Session {session_id} not found")

        ws = await self._ensure_ws(session_id)
        msg_id, msg = self._make_execute_request(code)
        await ws.send(json.dumps(msg))

        stdout = ""
        stderr = ""
        result = ""
        exit_code = 0

        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=self._execution_timeout)
                reply = json.loads(raw)

                # Only process messages that are responses to our request
                parent_msg_id = reply.get("parent_header", {}).get("msg_id")
                if parent_msg_id != msg_id:
                    continue

                msg_type = reply.get("msg_type", "")

                if msg_type == "stream":
                    name = reply["content"].get("name", "stdout")
                    text = reply["content"].get("text", "")
                    if name == "stderr":
                        stderr += text
                    else:
                        stdout += text

                elif msg_type == "error":
                    traceback_lines = reply["content"].get("traceback", [])
                    stderr += "\n".join(traceback_lines)
                    exit_code = 1

                elif msg_type == "execute_result":
                    data = reply["content"].get("data", {})
                    result = data.get("text/plain", "")

                elif msg_type == "execute_reply":
                    status = reply["content"].get("status", "ok")
                    if status == "error":
                        exit_code = 1

                elif msg_type == "status":
                    # status: idle on iopub signals all output has been sent.
                    # This is the reliable terminal condition — execute_reply
                    # on the shell channel can arrive before iopub messages.
                    if reply["content"].get("execution_state") == "idle":
                        break

        except TimeoutError:
            stderr += f"Execution timed out after {self._execution_timeout}s"
            exit_code = 1

        exec_result = {
            "session_id": session_id,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "result": result,
        }
        self._history.setdefault(session_id, []).append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "code": code,
                "language": language,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
                "result": result,
            }
        )
        return exec_result

    async def upload_file(
        self,
        session_id: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        if session_id not in self._sessions:
            raise ValueError(f"Session {session_id} not found")

        # Write file via kernel execution — works on both Jupyter Server
        # and Enterprise Gateway (which lacks the Contents REST API).
        encoded = base64.b64encode(content).decode("ascii")
        file_root = self._file_root
        code = (
            "import base64 as _b64, pathlib as _pl, os as _os\n"
            f"_p = _pl.Path(_os.path.join({file_root!r}, {filename!r}))\n"
            f"_p.parent.mkdir(parents=True, exist_ok=True)\n"
            f"_p.write_bytes(_b64.b64decode({encoded!r}))\n"
            f"print('ok')"
        )
        result = await self.execute(session_id, code)
        if result["exit_code"] != 0:
            raise RuntimeError(f"upload_file failed: {result['stderr']}")

        return {"filename": filename, "size": len(content), "session_id": session_id}

    async def download_file(self, session_id: str, filename: str) -> bytes:
        if session_id not in self._sessions:
            raise ValueError(f"Session {session_id} not found")

        # Read file via kernel execution — works on both Jupyter Server
        # and Enterprise Gateway (which lacks the Contents REST API).
        file_root = self._file_root
        code = (
            "import base64 as _b64, pathlib as _pl, os as _os\n"
            f"print(_b64.b64encode(_pl.Path(_os.path.join({file_root!r}, {filename!r})).read_bytes()).decode())"
        )
        result = await self.execute(session_id, code)
        if result["exit_code"] != 0:
            raise RuntimeError(f"download_file failed: {result['stderr']}")

        return base64.b64decode(result["stdout"].strip())

    async def list_sessions(self, status: str | None = None) -> list[dict[str, Any]]:
        return [
            {
                "session_id": sid,
                "status": SessionStatus.ACTIVE,
                "language": info["language"],
                "created_at": info["created_at"],
            }
            for sid, info in self._sessions.items()
        ]

    async def get_session(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        return {
            "session_id": session_id,
            "status": SessionStatus.ACTIVE,
            "language": session["language"],
            "created_at": session["created_at"],
        }

    async def get_execution_history(
        self,
        session_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if session_id not in self._sessions:
            raise KeyError(f"Session {session_id} not found")
        entries = self._history.get(session_id, [])
        return entries[-limit:]

    async def healthcheck(self) -> bool:
        try:
            async with httpx.AsyncClient(base_url=self._base_url, headers=self._http_headers()) as client:
                resp = await client.get("/api/kernels")
                resp.raise_for_status()
            return True
        except Exception:
            logger.exception("Jupyter healthcheck failed")
            return False
