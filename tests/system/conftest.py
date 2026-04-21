"""Shared fixtures for AgentCore system tests.

These tests exercise the full stack: AgenticPlatformClient → HTTP (ASGI) →
FastAPI middleware → route → registry → AgentCore provider → (mocked) AWS SDK.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentic_primitives_gateway import watcher as _watcher_module
from agentic_primitives_gateway.config import Settings
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway_client import AgenticPlatformClient

FAKE_AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
FAKE_AWS_REGION = "us-east-1"


@pytest.fixture(autouse=True)
def _reset_reload_error() -> None:
    """Clear any stale reload error between tests."""
    _watcher_module._last_reload_error = None


@pytest.fixture(autouse=True)
def _init_registry() -> None:
    """Override parent conftest — initialise registry with AgentCore providers.

    The observability provider's ``__init__`` calls ``_ensure_log_group``
    (creates a CloudWatch log group via boto3) and ``_setup_tracer`` (sets up
    OTel with SigV4 OTLP exporter).  Both are patched to avoid real AWS calls.
    """
    with (
        patch(
            "agentic_primitives_gateway.primitives.observability.agentcore."
            "AgentCoreObservabilityProvider._ensure_log_group"
        ),
        patch(
            "agentic_primitives_gateway.primitives.observability.agentcore."
            "AgentCoreObservabilityProvider._setup_tracer",
            return_value=MagicMock(),
        ),
    ):
        test_settings = Settings(
            allow_server_credentials="never",
            providers={
                "memory": {
                    "backend": ("agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider"),
                    "config": {"region": "us-east-1"},
                },
                "observability": {
                    "backend": (
                        "agentic_primitives_gateway.primitives.observability.agentcore.AgentCoreObservabilityProvider"
                    ),
                    "config": {
                        "region": "us-east-1",
                        "service_name": "test-svc",
                        "agent_id": "test-agent",
                    },
                },
                "llm": {
                    "backend": ("agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider"),
                    "config": {},
                },
                "tools": {
                    "backend": ("agentic_primitives_gateway.primitives.tools.agentcore.AgentCoreGatewayProvider"),
                    "config": {
                        "region": "us-east-1",
                        "gateway_url": "https://test-gw.example.com/mcp",
                    },
                },
                "identity": {
                    "backend": ("agentic_primitives_gateway.primitives.identity.agentcore.AgentCoreIdentityProvider"),
                    "config": {"region": "us-east-1"},
                },
                "code_interpreter": {
                    "backend": (
                        "agentic_primitives_gateway.primitives.code_interpreter.agentcore."
                        "AgentCoreCodeInterpreterProvider"
                    ),
                    "config": {"region": "us-east-1"},
                },
                "browser": {
                    "backend": ("agentic_primitives_gateway.primitives.browser.agentcore.AgentCoreBrowserProvider"),
                    "config": {"region": "us-east-1"},
                },
            },
        )
        registry.initialize(test_settings)


# ── Client fixture ────────────────────────────────────────────────────


@pytest.fixture
async def client() -> AgenticPlatformClient:
    """AgenticPlatformClient wired directly to the ASGI app (no TCP)."""
    transport = httpx.ASGITransport(app=app)
    async with AgenticPlatformClient(
        base_url="http://testserver",
        aws_access_key_id=FAKE_AWS_ACCESS_KEY,
        aws_secret_access_key=FAKE_AWS_SECRET_KEY,
        aws_region=FAKE_AWS_REGION,
        max_retries=0,
        transport=transport,
    ) as c:
        yield c


# ── Per-primitive mock fixtures ───────────────────────────────────────


@pytest.fixture
def mock_memory_manager():
    """Patch ``MemorySessionManager`` used by the AgentCore memory provider."""
    with patch("agentic_primitives_gateway.primitives.memory.agentcore.MemorySessionManager") as mock_cls:
        mock_mgr = MagicMock()
        mock_cls.return_value = mock_mgr
        yield mock_mgr


@pytest.fixture
def mock_memory_control_plane():
    """Patch ``_get_control_plane_client`` on the AgentCore memory provider.

    Control plane operations (create/get/list/delete memory resource and
    strategy management) now use a ``bedrock-agentcore-control`` boto3 client
    instead of ``MemorySessionManager``.
    """
    mock_cp = MagicMock()
    with patch(
        "agentic_primitives_gateway.primitives.memory.agentcore.AgentCoreMemoryProvider._get_control_plane_client",
        return_value=mock_cp,
    ):
        yield mock_cp


@pytest.fixture
def mock_identity_client():
    """Patch ``IdentityClient`` used by the AgentCore identity provider."""
    with patch("agentic_primitives_gateway.primitives.identity.agentcore.IdentityClient") as mock_cls:
        mock_client = MagicMock()
        mock_client.dp_client = MagicMock()
        mock_client.cp_client = MagicMock()
        mock_cls.return_value = mock_client
        yield mock_client


@pytest.fixture
def mock_code_interpreter():
    """Patch ``AgentCoreCodeInterpreter`` used by the code interpreter provider."""
    with patch("agentic_primitives_gateway.primitives.code_interpreter.agentcore.AgentCoreCodeInterpreter") as mock_cls:
        mock_ci = MagicMock()
        mock_cls.return_value = mock_ci
        yield mock_ci


@pytest.fixture
def mock_browser_client():
    """Patch ``BrowserClient`` used by the AgentCore browser provider."""
    with patch("agentic_primitives_gateway.primitives.browser.agentcore.BrowserClient") as mock_cls:
        mock_bc = MagicMock()
        mock_cls.return_value = mock_bc
        yield mock_bc


@pytest.fixture
def mock_xray_client():
    """Patch ``get_boto3_session`` in observability module → mock X-Ray client."""
    mock_session = MagicMock(region_name="us-east-1")
    mock_xray = MagicMock()
    mock_session.client.return_value = mock_xray
    with patch(
        "agentic_primitives_gateway.primitives.observability.agentcore.get_boto3_session",
        return_value=mock_session,
    ):
        yield mock_xray
