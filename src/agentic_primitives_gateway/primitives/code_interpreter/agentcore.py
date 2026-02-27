from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from bedrock_agentcore.tools import CodeInterpreter as AgentCoreCodeInterpreter

from agentic_primitives_gateway.context import get_aws_credentials, get_boto3_session
from agentic_primitives_gateway.models.enums import CodeLanguage, SessionStatus
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.code_interpreter.base import CodeInterpreterProvider

logger = logging.getLogger(__name__)


class AgentCoreCodeInterpreterProvider(SyncRunnerMixin, CodeInterpreterProvider):
    """Code interpreter backed by AWS Bedrock AgentCore Code Interpreter.

    AWS credentials are read from request context on every call. Each agent
    session is created with the caller's credentials.

    Provider config example::

        backend: agentic_primitives_gateway.primitives.code_interpreter.agentcore.AgentCoreCodeInterpreterProvider
        config:
          region: "us-east-1"
    """

    def __init__(self, region: str = "us-east-1", **kwargs: Any) -> None:
        self._region = region
        # session_id -> {client, created_at, language}
        self._sessions: dict[str, dict[str, Any]] = {}
        self._history: dict[str, list[dict[str, Any]]] = {}
        logger.info("AgentCore code interpreter provider initialized (region=%s)", region)

    def _get_region(self) -> str:
        creds = get_aws_credentials()
        if creds and creds.region:
            return creds.region
        return self._region

    async def start_session(
        self,
        session_id: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        boto_session = get_boto3_session(default_region=self._region)
        client = AgentCoreCodeInterpreter(region=boto_session.region_name)
        result = await self._run_sync(
            client.start,
            name=session_id,
        )
        # AgentCore returns a string session ID or a dict
        if isinstance(result, str):  # noqa: SIM108
            sid = result
        else:
            sid = result.get("sessionId", session_id or "unknown")
        self._sessions[sid] = {
            "client": client,
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
        if session:
            await self._run_sync(session["client"].stop)

    @staticmethod
    def _consume_stream(raw: dict[str, Any]) -> dict[str, Any]:
        """Consume the EventStream from invoke_code_interpreter and extract results."""
        structured: dict[str, Any] = {}
        stream = raw.get("stream")
        if stream:
            for event in stream:
                if "result" in event:
                    sc = event["result"].get("structuredContent") or {}
                    structured.update(sc)
        # Fall back to top-level fields if present (older SDK versions)
        return {
            "stdout": structured.get("stdout", raw.get("stdout", "")),
            "stderr": structured.get("stderr", raw.get("stderr", "")),
            "exitCode": structured.get("exitCode", raw.get("exitCode", 0)),
            "result": structured.get("result", raw.get("result")),
        }

    async def execute(
        self,
        session_id: str,
        code: str,
        language: str = CodeLanguage.PYTHON,
    ) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        def _execute() -> dict[str, Any]:
            raw = session["client"].execute_code(code=code, language=language)
            return self._consume_stream(raw)

        result = await self._run_sync(_execute)
        exec_result = {
            "session_id": session_id,
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "exit_code": result.get("exitCode", 0),
            "result": result.get("result"),
        }
        self._history.setdefault(session_id, []).append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "code": code,
                "language": language,
                "stdout": exec_result["stdout"],
                "stderr": exec_result["stderr"],
                "exit_code": exec_result["exit_code"],
                "result": exec_result["result"],
            }
        )
        return exec_result

    async def upload_file(
        self,
        session_id: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        await self._run_sync(session["client"].upload_file, path=filename, content=content)
        return {"filename": filename, "size": len(content), "session_id": session_id}

    async def download_file(self, session_id: str, filename: str) -> bytes:
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        result = await self._run_sync(session["client"].download_file, path=filename)
        if isinstance(result, bytes):
            return result
        if isinstance(result, str):
            return result.encode("utf-8")
        return str(result).encode("utf-8")

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
        return True
