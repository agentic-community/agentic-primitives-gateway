"""Caller AWS resources stay behind the effective request signer."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest

from agentic_primitives_gateway.config import settings
from agentic_primitives_gateway.context import (
    AWSCredentials,
    get_boto3_session,
    set_aws_credentials,
    set_service_credentials,
)
from agentic_primitives_gateway.models.enums import ServerCredentialMode
from agentic_primitives_gateway.primitives.knowledge.agentcore import AgentCoreKnowledgeProvider
from agentic_primitives_gateway.primitives.memory.agentcore import AgentCoreMemoryProvider


@pytest.fixture
def aws_mode() -> Iterator[None]:
    prev = settings.allow_server_credentials
    yield
    settings.allow_server_credentials = prev
    set_aws_credentials(None)
    set_service_credentials({})


def test_always_ignores_caller_aws_keys(aws_mode: None) -> None:
    settings.allow_server_credentials = ServerCredentialMode.ALWAYS
    set_aws_credentials(AWSCredentials(access_key_id="AKIACALLER", secret_access_key="caller-secret"))
    with patch("boto3.Session") as session_cls:
        get_boto3_session(default_region="us-east-1")
    assert session_cls.call_args.kwargs == {"region_name": "us-east-1"}


@pytest.mark.parametrize(
    ("cred_mode", "caller_aws", "expected"),
    [
        (ServerCredentialMode.NEVER, True, "caller-kb"),
        (ServerCredentialMode.FALLBACK, True, "caller-kb"),
        (ServerCredentialMode.FALLBACK, False, "op-kb"),
        (ServerCredentialMode.ALWAYS, True, "op-kb"),
    ],
)
def test_caller_resource_ids_only_under_caller_aws_credentials(
    aws_mode: None, cred_mode: ServerCredentialMode, caller_aws: bool, expected: str
) -> None:
    settings.allow_server_credentials = cred_mode
    if caller_aws:
        set_aws_credentials(AWSCredentials(access_key_id="AKIACALLER", secret_access_key="caller-secret"))
    set_service_credentials({"agentcore": {"knowledgebase_id": "caller-kb", "memory_id": "caller-kb"}})
    assert AgentCoreKnowledgeProvider(knowledge_base_id="op-kb")._resolve_knowledge_base_id() == expected
    assert AgentCoreMemoryProvider(memory_id="op-kb")._resolve_memory_id() == expected
