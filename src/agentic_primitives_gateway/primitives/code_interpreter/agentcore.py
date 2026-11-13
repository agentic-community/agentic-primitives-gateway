from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, TypeVar
from weakref import WeakValueDictionary

from bedrock_agentcore.tools import CodeInterpreter as AgentCoreCodeInterpreter

from agentic_primitives_gateway.context import get_aws_credentials, get_boto3_session
from agentic_primitives_gateway.models.enums import CodeLanguage, SessionStatus
from agentic_primitives_gateway.primitives._agentcore import rebind_agentcore_clients
from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin
from agentic_primitives_gateway.primitives.code_interpreter.base import CodeInterpreterProvider

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


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
        # Rebinding and SDK invocation must share one lock. Waiters keep it
        # alive across stop; otherwise idle session locks are reclaimed.
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
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
        client = AgentCoreCodeInterpreter(region=boto_session.region_name, session=boto_session)
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

    def _client(self, session_id: str) -> Any:
        """Return the session's SDK client rebound to the *current* request's AWS session.

        Sessions outlive the request that started them (and its possibly
        short-lived STS credentials), so every later call re-signs with the
        credentials in force now.  Only call while holding the session's lock
        (see ``_call``).
        """
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        client = session["client"]
        rebind_agentcore_clients(
            client,
            get_boto3_session(default_region=self._region),
            control_plane="control_plane_client",
            data_plane="data_plane_client",
            region=client.region,
        )
        return client

    async def _call(self, session_id: str, fn: Callable[[Any], _T], *, after: Callable[[], None] | None = None) -> _T:
        """Serialize SDK calls and rebinding through worker completion, even on cancellation.

        Binding in the worker keeps blocking credential resolution off the event
        loop while preserving the caller's contextvars.
        """
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        await lock.acquire()
        completion: asyncio.Task[_T] | None = None
        try:

            async def finish() -> _T:
                try:
                    result: _T = await self._run_sync(lambda: fn(self._client(session_id)))
                    if after is not None:
                        after()  # also runs when the request was cancelled
                    return result
                finally:
                    lock.release()

            completion = asyncio.create_task(finish())

            def consume(done: asyncio.Task[_T]) -> None:
                with suppress(asyncio.CancelledError):
                    done.exception()

            completion.add_done_callback(consume)
            return await asyncio.shield(completion)
        except BaseException:
            if completion is None:
                lock.release()  # task creation failed; no SDK worker exists
            raise

    async def stop_session(self, session_id: str) -> None:
        if session_id not in self._sessions:
            self._history.pop(session_id, None)
            return

        # Failed or cancelled stop retains the session until the SDK finishes.
        # A successful stop removes it before the lock admits the next waiter.
        def forget() -> None:
            self._sessions.pop(session_id, None)
            self._history.pop(session_id, None)

        await self._call(session_id, lambda c: c.stop(), after=forget)

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
        result = await self._call(
            session_id, lambda c: self._consume_stream(c.execute_code(code=code, language=language))
        )
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
        await self._call(session_id, lambda c: c.upload_file(path=filename, content=content))
        return {"filename": filename, "size": len(content), "session_id": session_id}

    async def download_file(self, session_id: str, filename: str) -> bytes:
        result = await self._call(session_id, lambda c: c.download_file(path=filename))
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

        def _read_history(client: Any) -> list[dict[str, Any]]:
            # _call rebinds the SDK to this request's AWS signer and holds the
            # session lock until the remote authorization check completes.
            remote = client.get_session(session_id=session_id)
            if (
                not isinstance(remote, dict)
                or remote.get("sessionId") != session_id
                or remote.get("status") != "READY"
                or (
                    remote.get("codeInterpreterIdentifier") and remote["codeInterpreterIdentifier"] != client.identifier
                )
            ):
                raise RuntimeError("Unable to verify AgentCore code interpreter session")
            return self._history.get(session_id, [])[-limit:]

        return await self._call(session_id, _read_history)

    async def healthcheck(self) -> bool:
        return True
