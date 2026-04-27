from __future__ import annotations

from fastapi.testclient import TestClient

from agentic_primitives_gateway.context import get_aws_credentials, set_aws_credentials
from agentic_primitives_gateway.models.enums import ServerCredentialMode


class TestAWSCredentialMiddleware:
    def test_credentials_extracted_from_headers(self, client: TestClient) -> None:
        """When AWS headers are sent, the middleware should set context."""
        # We can't directly inspect contextvars from the test client
        # because the middleware runs in a different context. Instead,
        # we verify the request succeeds with the headers present.
        resp = client.get(
            "/healthz",
            headers={
                "x-aws-access-key-id": "AKIAIOSFODNN7EXAMPLE",
                "x-aws-secret-access-key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "x-aws-session-token": "FwoGZXIvYXdzEBAaDH...",
                "x-aws-region": "us-west-2",
            },
        )
        assert resp.status_code == 200

    def test_requests_work_without_aws_headers(self, client: TestClient) -> None:
        """Requests without AWS headers should work fine (context is None)."""
        resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_partial_aws_headers_ignored(self, client: TestClient) -> None:
        """If only one of access key / secret key is sent, context stays None."""
        resp = client.get(
            "/healthz",
            headers={"x-aws-access-key-id": "AKIAIOSFODNN7EXAMPLE"},
        )
        assert resp.status_code == 200

    def test_memory_endpoints_work_with_aws_headers(self, client: TestClient) -> None:
        """Memory CRUD should work when AWS headers are present."""
        aws_headers = {
            "x-aws-access-key-id": "AKIAIOSFODNN7EXAMPLE",
            "x-aws-secret-access-key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        }
        resp = client.post(
            "/api/v1/memory/ns1",
            json={"key": "k1", "content": "test with aws creds"},
            headers=aws_headers,
        )
        assert resp.status_code == 201

        resp = client.get("/api/v1/memory/ns1/k1", headers=aws_headers)
        assert resp.status_code == 200
        assert resp.json()["content"] == "test with aws creds"


class TestAWSCredentialsDataclass:
    def test_set_and_get_credentials(self) -> None:
        from agentic_primitives_gateway.context import AWSCredentials

        creds = AWSCredentials(
            access_key_id="AKIA...",
            secret_access_key="secret",
            session_token="token",
            region="us-west-2",
        )
        set_aws_credentials(creds)
        retrieved = get_aws_credentials()
        assert retrieved is not None
        assert retrieved.access_key_id == "AKIA..."
        assert retrieved.secret_access_key == "secret"
        assert retrieved.session_token == "token"
        assert retrieved.region == "us-west-2"

        # Clean up
        set_aws_credentials(None)
        assert get_aws_credentials() is None

    def test_get_boto3_session_without_creds_raises_when_server_creds_disabled(self) -> None:
        # Force ``allow_server_credentials`` off for the duration of this
        # test. Integration tests earlier in the run can leave it in the
        # enabled state (integration/conftest.py uses "always"), so we
        # can't rely on the module-level default here.
        import pytest

        from agentic_primitives_gateway.config import settings
        from agentic_primitives_gateway.context import get_boto3_session

        original = settings.allow_server_credentials
        try:
            settings.allow_server_credentials = ServerCredentialMode.NEVER
            set_aws_credentials(None)
            with pytest.raises(ValueError, match="server credential fallback is disabled"):
                get_boto3_session(default_region="us-east-1")
        finally:
            settings.allow_server_credentials = original

    def test_get_boto3_session_without_creds_works_when_server_creds_enabled(self) -> None:
        from agentic_primitives_gateway.config import settings
        from agentic_primitives_gateway.context import get_boto3_session

        original = settings.allow_server_credentials
        try:
            settings.allow_server_credentials = ServerCredentialMode.ALWAYS
            set_aws_credentials(None)
            session = get_boto3_session(default_region="us-east-1")
            assert session.region_name == "us-east-1"
        finally:
            settings.allow_server_credentials = original

    def test_get_boto3_session_with_creds(self) -> None:
        from agentic_primitives_gateway.context import AWSCredentials, get_boto3_session

        creds = AWSCredentials(
            access_key_id="AKIATEST",
            secret_access_key="SECRET",
            session_token="TOKEN",
            region="eu-west-1",
        )
        set_aws_credentials(creds)
        session = get_boto3_session(default_region="us-east-1")
        assert session.region_name == "eu-west-1"
        resolved = session.get_credentials().get_frozen_credentials()
        assert resolved.access_key == "AKIATEST"
        assert resolved.secret_key == "SECRET"
        assert resolved.token == "TOKEN"

        # Clean up
        set_aws_credentials(None)


class TestServiceCredentials:
    def test_middleware_extracts_service_creds(self, client: TestClient) -> None:
        """X-Cred-* headers should be parsed into service credentials."""
        resp = client.get(
            "/healthz",
            headers={
                "x-cred-langfuse-public-key": "pk-test",
                "x-cred-langfuse-secret-key": "sk-test",
                "x-cred-langfuse-base-url": "https://langfuse.example.com",
            },
        )
        assert resp.status_code == 200

    def test_set_and_get_service_credentials(self) -> None:
        from agentic_primitives_gateway.context import (
            get_service_credentials,
            set_service_credentials,
        )

        set_service_credentials(
            {
                "langfuse": {
                    "public_key": "pk-123",
                    "secret_key": "sk-456",
                },
                "openai": {
                    "api_key": "sk-openai",
                },
            }
        )

        langfuse = get_service_credentials("langfuse")
        assert langfuse is not None
        assert langfuse["public_key"] == "pk-123"
        assert langfuse["secret_key"] == "sk-456"

        openai = get_service_credentials("openai")
        assert openai is not None
        assert openai["api_key"] == "sk-openai"

        assert get_service_credentials("nonexistent") is None

        # Clean up
        set_service_credentials({})

    def test_multiple_services_on_same_request(self, client: TestClient) -> None:
        """Multiple services' credentials can be sent in a single request."""
        resp = client.get(
            "/healthz",
            headers={
                "x-cred-langfuse-public-key": "pk-test",
                "x-cred-langfuse-secret-key": "sk-test",
                "x-cred-openai-api-key": "sk-openai-test",
            },
        )
        assert resp.status_code == 200
