"""Integration tests for the AgentCore code interpreter primitive.

Full stack with real AWS calls:
AgenticPlatformClient → ASGI → middleware → route → registry →
AgentCoreCodeInterpreterProvider → real AgentCoreCodeInterpreter SDK.

Requires: AWS credentials.  Sessions are self-provisioned via fixture.
"""

from __future__ import annotations

import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration


# ── Code execution ───────────────────────────────────────────────────


class TestExecuteSimple:
    async def test_execute_simple(self, client: AgenticPlatformClient, code_session: str) -> None:
        result = await client.execute_code(code_session, "print(1+1)")

        assert result["session_id"] == code_session
        assert "2" in result["stdout"]
        assert result["exit_code"] == 0


class TestExecuteMultiline:
    async def test_execute_multiline(self, client: AgenticPlatformClient, code_session: str) -> None:
        code = """\
items = [1, 2, 3, 4, 5]
total = sum(items)
print(f"sum={total}")
"""
        result = await client.execute_code(code_session, code)

        assert "sum=15" in result["stdout"]
        assert result["exit_code"] == 0


class TestExecuteError:
    async def test_execute_error(self, client: AgenticPlatformClient, code_session: str) -> None:
        result = await client.execute_code(code_session, "raise ValueError('boom')")

        # Errors should show up in stderr or have non-zero exit code
        has_error = result.get("stderr", "") != "" or result.get("exit_code", 0) != 0
        assert has_error


# ── File I/O ─────────────────────────────────────────────────────────


class TestUploadAndUseFile:
    async def test_upload_and_use_file(self, client: AgenticPlatformClient, code_session: str) -> None:
        # Write a file via execute_code (most reliable approach)
        write_result = await client.execute_code(
            code_session,
            """\
with open("data.csv", "w") as f:
    f.write("name,value\\nalpha,1\\nbeta,2\\ngamma,3\\n")
print("written")
""",
        )

        assert "written" in write_result["stdout"]

        # Execute code that reads the file back
        result = await client.execute_code(
            code_session,
            """\
with open("data.csv") as f:
    lines = f.readlines()
print(len(lines))
""",
        )

        assert "4" in result["stdout"]  # header + 3 data rows


class TestDownloadFile:
    async def test_download_file(self, client: AgenticPlatformClient, code_session: str) -> None:
        # Write a file in the session
        await client.execute_code(
            code_session,
            """\
with open("output.txt", "w") as f:
    f.write("hello from code interpreter")
""",
        )

        content = await client.download_file(code_session, "output.txt")

        assert b"hello from code interpreter" in content


# ── Session management ───────────────────────────────────────────────


class TestSessionLifecycle:
    async def test_session_lifecycle(self, client: AgenticPlatformClient) -> None:
        """Start, get, list, stop — no fixture."""
        # Start
        started = await client.start_code_session()
        sid = started["session_id"]
        assert started["status"] == "active"

        try:
            # Get
            session = await client.get_code_session(sid)
            assert session["session_id"] == sid
            assert session["status"] == "active"

            # List
            listed = await client.list_code_sessions()
            assert "sessions" in listed
            ids = [s["session_id"] for s in listed["sessions"]]
            assert sid in ids
        finally:
            # Stop
            await client.stop_code_session(sid)


# ── Execution history ────────────────────────────────────────────────


class TestExecutionHistory:
    async def test_execution_history(self, client: AgenticPlatformClient, code_session: str) -> None:
        await client.execute_code(code_session, "print('first')")
        await client.execute_code(code_session, "print('second')")
        await client.execute_code(code_session, "print('third')")

        result = await client.get_execution_history(code_session)

        assert "entries" in result
        assert len(result["entries"]) >= 3
