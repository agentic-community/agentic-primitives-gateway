"""Integration tests for the Jupyter code interpreter primitive.

Full stack with real Jupyter calls:
AgenticPlatformClient → ASGI → middleware → route → registry →
JupyterCodeInterpreterProvider → real Jupyter Server.

Requires:
  - JUPYTER_URL env var (e.g. http://localhost:8888)
  - Optionally JUPYTER_TOKEN
  - Optionally JUPYTER_KERNEL (default: python3)
"""

from __future__ import annotations

import contextlib
import os

import httpx
import pytest

import agentic_primitives_gateway.config as _config_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient

pytestmark = pytest.mark.integration


# ── Skip logic ────────────────────────────────────────────────────────

if not os.environ.get("JUPYTER_URL"):
    pytest.skip(
        "JUPYTER_URL not set — skipping Jupyter integration tests",
        allow_module_level=True,
    )

FAKE_AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
FAKE_AWS_REGION = "us-east-1"


# ── Registry initialization ──────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_registry():
    """Initialise registry with Jupyter code interpreter provider (noop for everything else).

    Jupyter credentials are read from env vars and baked into the provider config
    so the provider doesn't need per-request credential headers.
    """
    base_url = os.environ["JUPYTER_URL"]
    token = os.environ.get("JUPYTER_TOKEN", "")
    kernel_name = os.environ.get("JUPYTER_KERNEL", "python3")

    code_interpreter_config: dict[str, str | float] = {
        "base_url": base_url,
        "token": token,
        "kernel_name": kernel_name,
        "execution_timeout": 30.0,
    }

    test_settings = Settings(
        allow_server_credentials=True,
        providers={
            "memory": {
                "backend": "agentic_primitives_gateway.primitives.memory.noop.NoopMemoryProvider",
                "config": {},
            },
            "observability": {
                "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider",
                "config": {},
            },
            "llm": {
                "backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider",
                "config": {},
            },
            "tools": {
                "backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider",
                "config": {},
            },
            "identity": {
                "backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider",
                "config": {},
            },
            "code_interpreter": {
                "backend": (
                    "agentic_primitives_gateway.primitives.code_interpreter.jupyter.JupyterCodeInterpreterProvider"
                ),
                "config": code_interpreter_config,
            },
            "browser": {
                "backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider",
                "config": {},
            },
        },
    )
    orig_settings = _config_module.settings
    _config_module.settings = test_settings
    registry.initialize(test_settings)

    yield

    _config_module.settings = orig_settings


# ── Client fixture ───────────────────────────────────────────────────


@pytest.fixture
async def client():
    """AgenticPlatformClient wired to ASGI app with fake AWS creds.

    Jupyter doesn't need AWS credentials — they're baked into the provider
    config. We use fake AWS creds to satisfy the middleware.
    """
    transport = httpx.ASGITransport(app=app)
    async with AgenticPlatformClient(
        base_url="http://testserver",
        aws_access_key_id=FAKE_AWS_ACCESS_KEY,
        aws_secret_access_key=FAKE_AWS_SECRET_KEY,
        aws_region=FAKE_AWS_REGION,
        max_retries=2,
        transport=transport,
    ) as c:
        yield c


# ── Session fixture ──────────────────────────────────────────────────


@pytest.fixture
async def code_session(client: AgenticPlatformClient):
    """Start a code interpreter session, yield ID, stop on teardown."""
    result = await client.start_code_session()
    sid = result["session_id"]
    yield sid
    with contextlib.suppress(Exception):
        await client.stop_code_session(sid)


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


class TestFileViaKernel:
    async def test_write_and_read_file_in_kernel(self, client: AgenticPlatformClient, code_session: str) -> None:
        """Write to /tmp (writable) and read it back — all via kernel execution."""
        write_result = await client.execute_code(
            code_session,
            """\
with open("/tmp/data.csv", "w") as f:
    f.write("name,value\\nalpha,1\\nbeta,2\\ngamma,3\\n")
print("written")
""",
        )

        assert "written" in write_result["stdout"]

        result = await client.execute_code(
            code_session,
            """\
with open("/tmp/data.csv") as f:
    lines = f.readlines()
print(len(lines))
""",
        )

        assert "4" in result["stdout"]  # header + 3 data rows


class TestUploadAndDownloadFile:
    async def test_upload_and_download_file(self, client: AgenticPlatformClient, code_session: str) -> None:
        """Upload a file via the API, then download it back and verify contents."""
        original = b"col1,col2\n1,2\n3,4\n"
        upload_result = await client.upload_file(code_session, "test_upload.csv", original)

        assert upload_result["filename"] == "test_upload.csv"
        assert upload_result["size"] == len(original)

        downloaded = await client.download_file(code_session, "test_upload.csv")

        assert downloaded == original


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
