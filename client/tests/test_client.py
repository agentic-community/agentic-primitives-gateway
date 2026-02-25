from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient, AgenticPlatformError


class TestClientMemory:
    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, make_client) -> None:
        async with make_client() as client:
            record = await client.store_memory("ns1", "k1", "hello world")
            assert record["namespace"] == "ns1"
            assert record["key"] == "k1"
            assert record["content"] == "hello world"

            retrieved = await client.retrieve_memory("ns1", "k1")
            assert retrieved["content"] == "hello world"

    @pytest.mark.asyncio
    async def test_store_with_metadata(self, make_client) -> None:
        async with make_client() as client:
            record = await client.store_memory("ns1", "k1", "content", metadata={"source": "test"})
            assert record["metadata"] == {"source": "test"}

    @pytest.mark.asyncio
    async def test_search(self, make_client) -> None:
        async with make_client() as client:
            await client.store_memory("ns1", "k1", "python programming")
            await client.store_memory("ns1", "k2", "sunny weather")

            results = await client.search_memory("ns1", "programming")
            assert len(results["results"]) == 1
            assert results["results"][0]["record"]["key"] == "k1"

    @pytest.mark.asyncio
    async def test_delete(self, make_client) -> None:
        async with make_client() as client:
            await client.store_memory("ns1", "k1", "to delete")
            await client.delete_memory("ns1", "k1")

            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.retrieve_memory("ns1", "k1")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_memories(self, make_client) -> None:
        async with make_client() as client:
            await client.store_memory("ns1", "a", "aaa")
            await client.store_memory("ns1", "b", "bbb")

            result = await client.list_memories("ns1")
            assert result["total"] == 2

    @pytest.mark.asyncio
    async def test_retrieve_not_found(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.retrieve_memory("ns1", "nonexistent")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_not_found(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.delete_memory("ns1", "nonexistent")
            assert exc_info.value.status_code == 404


class TestClientHealth:
    @pytest.mark.asyncio
    async def test_healthz(self, make_client) -> None:
        async with make_client() as client:
            result = await client.healthz()
            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readyz(self, make_client) -> None:
        async with make_client() as client:
            result = await client.readyz()
            assert result["status"] == "ok"


class TestClientStubs:
    """Verify the client raises AgenticPlatformError for 501 stub endpoints."""

    @pytest.mark.asyncio
    async def test_identity_control_plane_stub(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.create_credential_provider("test", "oauth2")
            assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_code_interpreter_stub(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.start_code_session()
            assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_browser_stub(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.start_browser_session()
            assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_observability_stub(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.ingest_trace({"trace_id": "t1"})
            assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_gateway_stub(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.completions({"model": "test"})
            assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_tools_stub(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.list_tools()
            assert exc_info.value.status_code == 501


class TestClientIdentity:
    """Tests for the identity data plane methods."""

    @pytest.mark.asyncio
    async def test_get_token(self, make_client) -> None:
        async with make_client() as client:
            result = await client.get_token("github", "wt-123", scopes=["repo"])
            assert result["access_token"] == "mock-token"
            assert result["token_type"] == "Bearer"

    @pytest.mark.asyncio
    async def test_get_api_key(self, make_client) -> None:
        async with make_client() as client:
            result = await client.get_api_key("openai", "wt-123")
            assert result["api_key"] == "mock-api-key"
            assert result["credential_provider"] == "openai"

    @pytest.mark.asyncio
    async def test_get_workload_token(self, make_client) -> None:
        async with make_client() as client:
            result = await client.get_workload_token("my-agent")
            assert result["workload_token"] == "mock-workload-token"
            assert result["workload_name"] == "my-agent"

    @pytest.mark.asyncio
    async def test_list_credential_providers(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_credential_providers()
            assert result["credential_providers"] == []


class TestClientContextManager:
    @pytest.mark.asyncio
    async def test_async_context_manager(self) -> None:
        """Verify the client can be used as an async context manager."""
        import httpx

        transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"status": "ok"}))
        async with AgenticPlatformClient(base_url="http://test", transport=transport) as client:
            result = await client.healthz()
            assert result["status"] == "ok"


class TestClientErrorHandling:
    @pytest.mark.asyncio
    async def test_error_includes_status_and_detail(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.retrieve_memory("ns1", "missing")
            err = exc_info.value
            assert err.status_code == 404
            assert "Memory not found" in err.detail
            assert "404" in str(err)


class TestAWSCredentials:
    def test_explicit_credentials_set_headers(self) -> None:
        client = AgenticPlatformClient.__new__(AgenticPlatformClient)
        client._aws_headers = {}
        client._aws_from_environment = False
        client._provider_headers = {}
        client._service_cred_headers = {}
        client.set_aws_credentials(
            access_key_id="AKIA_TEST",
            secret_access_key="SECRET_TEST",
            session_token="TOKEN_TEST",
            region="us-west-2",
        )
        headers = client._headers
        assert headers["x-aws-access-key-id"] == "AKIA_TEST"
        assert headers["x-aws-secret-access-key"] == "SECRET_TEST"
        assert headers["x-aws-session-token"] == "TOKEN_TEST"
        assert headers["x-aws-region"] == "us-west-2"

    def test_clear_credentials(self) -> None:
        client = AgenticPlatformClient.__new__(AgenticPlatformClient)
        client._aws_headers = {"x-aws-access-key-id": "AKIA"}
        client._aws_from_environment = False
        client._provider_headers = {}
        client._service_cred_headers = {}
        client.clear_aws_credentials()
        assert client._headers == {}

    def test_no_credentials_returns_empty_headers(self) -> None:
        client = AgenticPlatformClient.__new__(AgenticPlatformClient)
        client._aws_headers = {}
        client._aws_from_environment = False
        client._provider_headers = {}
        client._service_cred_headers = {}
        assert client._headers == {}

    def test_aws_from_environment_resolves_credentials(self) -> None:
        """Verify aws_from_environment reads from boto3 credential chain."""
        from unittest.mock import MagicMock, patch

        mock_creds = MagicMock()
        mock_creds.access_key = "AKIA_FROM_ENV"
        mock_creds.secret_key = "SECRET_FROM_ENV"
        mock_creds.token = "TOKEN_FROM_ENV"

        mock_resolved = MagicMock()
        mock_resolved.get_frozen_credentials.return_value = mock_creds

        mock_session = MagicMock()
        mock_session.get_credentials.return_value = mock_resolved
        mock_session.region_name = "ap-southeast-1"

        client = AgenticPlatformClient.__new__(AgenticPlatformClient)
        client._aws_headers = {}
        client._aws_from_environment = True
        client._aws_profile = None
        client._aws_env_region = None
        client._provider_headers = {}
        client._service_cred_headers = {}

        with patch("boto3.Session", return_value=mock_session):
            headers = client._headers

        assert headers["x-aws-access-key-id"] == "AKIA_FROM_ENV"
        assert headers["x-aws-secret-access-key"] == "SECRET_FROM_ENV"
        assert headers["x-aws-session-token"] == "TOKEN_FROM_ENV"
        assert headers["x-aws-region"] == "ap-southeast-1"

    def test_aws_from_environment_region_override(self) -> None:
        """Explicit aws_region overrides boto3 session region."""
        from unittest.mock import MagicMock, patch

        mock_creds = MagicMock()
        mock_creds.access_key = "AKIA"
        mock_creds.secret_key = "SECRET"
        mock_creds.token = None

        mock_resolved = MagicMock()
        mock_resolved.get_frozen_credentials.return_value = mock_creds

        mock_session = MagicMock()
        mock_session.get_credentials.return_value = mock_resolved
        mock_session.region_name = "us-east-1"

        client = AgenticPlatformClient.__new__(AgenticPlatformClient)
        client._aws_headers = {}
        client._aws_from_environment = True
        client._aws_profile = None
        client._aws_env_region = "eu-west-1"  # explicit override
        client._provider_headers = {}
        client._service_cred_headers = {}

        with patch("boto3.Session", return_value=mock_session):
            headers = client._headers

        assert headers["x-aws-region"] == "eu-west-1"
        assert "x-aws-session-token" not in headers  # token was None

    def test_aws_from_environment_no_credentials(self) -> None:
        """When boto3 has no credentials, return empty headers."""
        from unittest.mock import MagicMock, patch

        mock_session = MagicMock()
        mock_session.get_credentials.return_value = None

        client = AgenticPlatformClient.__new__(AgenticPlatformClient)
        client._aws_headers = {}
        client._aws_from_environment = True
        client._aws_profile = None
        client._aws_env_region = None
        client._provider_headers = {}
        client._service_cred_headers = {}

        with patch("boto3.Session", return_value=mock_session):
            headers = client._headers

        assert headers == {}

    @pytest.mark.asyncio
    async def test_aws_headers_sent_on_requests(self) -> None:
        """Verify AWS headers are actually included in HTTP requests."""
        import httpx

        received_headers = {}

        def capture_handler(request: httpx.Request) -> httpx.Response:
            received_headers.update(dict(request.headers))
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(capture_handler)
        async with AgenticPlatformClient(
            base_url="http://test",
            aws_access_key_id="AKIA_SENT",
            aws_secret_access_key="SECRET_SENT",
            aws_session_token="TOKEN_SENT",
            transport=transport,
        ) as client:
            await client.healthz()

        assert received_headers["x-aws-access-key-id"] == "AKIA_SENT"
        assert received_headers["x-aws-secret-access-key"] == "SECRET_SENT"
        assert received_headers["x-aws-session-token"] == "TOKEN_SENT"


class TestRetryLogic:
    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway_client.client.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_transport_error_then_success(self, mock_sleep) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(handler)
        async with AgenticPlatformClient(
            base_url="http://test",
            max_retries=3,
            retry_backoff=0.0,
            transport=transport,
        ) as client:
            result = await client.healthz()

        assert result["status"] == "ok"
        assert call_count == 3
        assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway_client.client.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_on_503_then_success(self, mock_sleep) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return httpx.Response(503, text="Service Unavailable")
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(handler)
        async with AgenticPlatformClient(
            base_url="http://test",
            max_retries=3,
            retry_backoff=0.0,
            transport=transport,
        ) as client:
            result = await client.healthz()

        assert result["status"] == "ok"
        assert call_count == 2
        assert mock_sleep.call_count == 1

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway_client.client.asyncio.sleep", new_callable=AsyncMock)
    async def test_no_retry_on_4xx(self, mock_sleep) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(404, json={"detail": "Not found"})

        transport = httpx.MockTransport(handler)
        async with AgenticPlatformClient(
            base_url="http://test",
            max_retries=3,
            retry_backoff=0.0,
            transport=transport,
        ) as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.healthz()
            assert exc_info.value.status_code == 404

        assert call_count == 1
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway_client.client.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_exhaustion_raises_transport_error(self, mock_sleep) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        transport = httpx.MockTransport(handler)
        async with AgenticPlatformClient(
            base_url="http://test",
            max_retries=2,
            retry_backoff=0.0,
            transport=transport,
        ) as client:
            with pytest.raises(httpx.ConnectError):
                await client.healthz()

        assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    @patch("agentic_primitives_gateway_client.client.asyncio.sleep", new_callable=AsyncMock)
    async def test_retry_exhaustion_returns_last_response(self, mock_sleep) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Service Unavailable")

        transport = httpx.MockTransport(handler)
        async with AgenticPlatformClient(
            base_url="http://test",
            max_retries=2,
            retry_backoff=0.0,
            transport=transport,
        ) as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.healthz()
            assert exc_info.value.status_code == 503

        assert mock_sleep.call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_zero_disables_retries(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(503, text="Service Unavailable")

        transport = httpx.MockTransport(handler)
        async with AgenticPlatformClient(
            base_url="http://test",
            max_retries=0,
            transport=transport,
        ) as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.healthz()
            assert exc_info.value.status_code == 503

        assert call_count == 1
