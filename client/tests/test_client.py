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


class TestClientConversationEvents:
    @pytest.mark.asyncio
    async def test_create_event(self, make_client) -> None:
        async with make_client() as client:
            result = await client.create_event(
                "actor-1",
                "sess-1",
                [{"text": "Hello", "role": "user"}],
            )
            assert result["actor_id"] == "actor-1"
            assert result["session_id"] == "sess-1"
            assert "event_id" in result

    @pytest.mark.asyncio
    async def test_list_events(self, make_client) -> None:
        async with make_client() as client:
            await client.create_event("a1", "s1", [{"text": "hi", "role": "user"}])
            result = await client.list_events("a1", "s1")
            assert len(result["events"]) == 1

    @pytest.mark.asyncio
    async def test_get_event(self, make_client) -> None:
        async with make_client() as client:
            created = await client.create_event("a1", "s1", [{"text": "hi", "role": "user"}])
            result = await client.get_event("a1", "s1", created["event_id"])
            assert result["event_id"] == created["event_id"]

    @pytest.mark.asyncio
    async def test_delete_event(self, make_client) -> None:
        async with make_client() as client:
            created = await client.create_event("a1", "s1", [{"text": "hi", "role": "user"}])
            await client.delete_event("a1", "s1", created["event_id"])
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.get_event("a1", "s1", created["event_id"])
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_last_turns(self, make_client) -> None:
        async with make_client() as client:
            await client.create_event("a1", "s1", [{"text": "turn1", "role": "user"}])
            await client.create_event("a1", "s1", [{"text": "turn2", "role": "assistant"}])
            result = await client.get_last_turns("a1", "s1", k=5)
            assert len(result["turns"]) == 2


class TestClientSessionManagement:
    @pytest.mark.asyncio
    async def test_list_actors(self, make_client) -> None:
        async with make_client() as client:
            await client.create_event("a1", "s1", [{"text": "hi", "role": "user"}])
            result = await client.list_actors()
            actor_ids = [a["actor_id"] for a in result["actors"]]
            assert "a1" in actor_ids

    @pytest.mark.asyncio
    async def test_list_memory_sessions(self, make_client) -> None:
        async with make_client() as client:
            await client.create_event("a1", "s1", [{"text": "hi", "role": "user"}])
            result = await client.list_memory_sessions("a1")
            session_ids = [s["session_id"] for s in result["sessions"]]
            assert "s1" in session_ids


class TestClientBranchManagement:
    @pytest.mark.asyncio
    async def test_fork_conversation(self, make_client) -> None:
        async with make_client() as client:
            result = await client.fork_conversation(
                "a1",
                "s1",
                "evt-1",
                "branch-1",
                [{"text": "hello", "role": "user"}],
            )
            assert result["name"] == "branch-1"

    @pytest.mark.asyncio
    async def test_list_branches(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_branches("a1", "s1")
            assert "branches" in result


class TestClientControlPlane:
    @pytest.mark.asyncio
    async def test_create_memory_resource(self, make_client) -> None:
        async with make_client() as client:
            result = await client.create_memory_resource("test-mem")
            assert result["memory_id"] == "mem-new"
            assert result["name"] == "test-mem"

    @pytest.mark.asyncio
    async def test_get_memory_resource(self, make_client) -> None:
        async with make_client() as client:
            result = await client.get_memory_resource("mem-1")
            assert result["memory_id"] == "mem-1"

    @pytest.mark.asyncio
    async def test_list_memory_resources(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_memory_resources()
            assert "resources" in result

    @pytest.mark.asyncio
    async def test_delete_memory_resource(self, make_client) -> None:
        async with make_client() as client:
            await client.delete_memory_resource("mem-1")


class TestClientStrategyManagement:
    @pytest.mark.asyncio
    async def test_list_strategies(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_strategies("mem-1")
            assert "strategies" in result

    @pytest.mark.asyncio
    async def test_add_strategy(self, make_client) -> None:
        async with make_client() as client:
            result = await client.add_strategy("mem-1", {"type": "semantic"})
            assert result["strategy_id"] == "strat-1"

    @pytest.mark.asyncio
    async def test_delete_strategy(self, make_client) -> None:
        async with make_client() as client:
            await client.delete_strategy("mem-1", "strat-1")


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


class TestClientObservability:
    @pytest.mark.asyncio
    async def test_ingest_trace(self, make_client) -> None:
        async with make_client() as client:
            result = await client.ingest_trace({"trace_id": "t-1", "name": "test"})
            assert result["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_ingest_log(self, make_client) -> None:
        async with make_client() as client:
            result = await client.ingest_log({"level": "info", "message": "test"})
            assert result["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_query_traces(self, make_client) -> None:
        async with make_client() as client:
            result = await client.query_traces()
            assert "traces" in result

    @pytest.mark.asyncio
    async def test_get_trace(self, make_client) -> None:
        async with make_client() as client:
            result = await client.get_trace("t-1")
            assert result["trace_id"] == "t-1"

    @pytest.mark.asyncio
    async def test_update_trace(self, make_client) -> None:
        async with make_client() as client:
            result = await client.update_trace("t-1", {"name": "updated"})
            assert result["trace_id"] == "t-1"

    @pytest.mark.asyncio
    async def test_log_generation(self, make_client) -> None:
        async with make_client() as client:
            result = await client.log_generation("t-1", {"name": "chat", "model": "claude-3"})
            assert result["generation_id"] == "gen-mock"

    @pytest.mark.asyncio
    async def test_score_trace(self, make_client) -> None:
        async with make_client() as client:
            result = await client.score_trace("t-1", {"name": "quality", "value": 0.95})
            assert result["score_id"] == "score-mock"

    @pytest.mark.asyncio
    async def test_list_scores(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_scores("t-1")
            assert "scores" in result

    @pytest.mark.asyncio
    async def test_list_observability_sessions(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_observability_sessions()
            assert "sessions" in result

    @pytest.mark.asyncio
    async def test_get_observability_session(self, make_client) -> None:
        async with make_client() as client:
            result = await client.get_observability_session("sess-1")
            assert result["session_id"] == "sess-1"

    @pytest.mark.asyncio
    async def test_flush_observability(self, make_client) -> None:
        async with make_client() as client:
            result = await client.flush_observability()
            assert result["status"] == "accepted"


class TestClientTools:
    @pytest.mark.asyncio
    async def test_register_tool(self, make_client) -> None:
        async with make_client() as client:
            result = await client.register_tool({"name": "search", "description": "Search the web"})
            assert result["name"] == "search"

    @pytest.mark.asyncio
    async def test_list_tools(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_tools()
            assert "tools" in result

    @pytest.mark.asyncio
    async def test_invoke_tool(self, make_client) -> None:
        async with make_client() as client:
            result = await client.invoke_tool("search", {"query": "test"})
            assert result["tool_name"] == "search"

    @pytest.mark.asyncio
    async def test_search_tools(self, make_client) -> None:
        async with make_client() as client:
            result = await client.search_tools("search")
            assert "tools" in result

    @pytest.mark.asyncio
    async def test_get_tool(self, make_client) -> None:
        async with make_client() as client:
            result = await client.get_tool("my-tool")
            assert result["name"] == "my-tool"

    @pytest.mark.asyncio
    async def test_delete_tool(self, make_client) -> None:
        async with make_client() as client:
            await client.delete_tool("my-tool")

    @pytest.mark.asyncio
    async def test_list_tool_servers(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_tool_servers()
            assert "servers" in result

    @pytest.mark.asyncio
    async def test_get_tool_server(self, make_client) -> None:
        async with make_client() as client:
            result = await client.get_tool_server("calc-server")
            assert result["name"] == "calc-server"

    @pytest.mark.asyncio
    async def test_register_tool_server(self, make_client) -> None:
        async with make_client() as client:
            result = await client.register_tool_server({"name": "new-server", "url": "http://new:9000"})
            assert result["name"] == "new-server"


class TestClientCodeInterpreter:
    @pytest.mark.asyncio
    async def test_start_code_session(self, make_client) -> None:
        async with make_client() as client:
            result = await client.start_code_session(session_id="s-1")
            assert result["session_id"] == "s-1"

    @pytest.mark.asyncio
    async def test_stop_code_session(self, make_client) -> None:
        async with make_client() as client:
            await client.start_code_session(session_id="s-1")
            await client.stop_code_session("s-1")

    @pytest.mark.asyncio
    async def test_execute_code(self, make_client) -> None:
        async with make_client() as client:
            await client.start_code_session(session_id="s-1")
            result = await client.execute_code("s-1", "print(1)")
            assert result["session_id"] == "s-1"

    @pytest.mark.asyncio
    async def test_list_code_sessions(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_code_sessions()
            assert "sessions" in result

    @pytest.mark.asyncio
    async def test_get_code_session(self, make_client) -> None:
        async with make_client() as client:
            await client.start_code_session(session_id="s-1")
            result = await client.get_code_session("s-1")
            assert result["session_id"] == "s-1"

    @pytest.mark.asyncio
    async def test_get_code_session_not_found(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.get_code_session("nonexistent")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_execution_history(self, make_client) -> None:
        async with make_client() as client:
            await client.start_code_session(session_id="s-1")
            result = await client.get_execution_history("s-1")
            assert "entries" in result

    @pytest.mark.asyncio
    async def test_get_execution_history_not_found(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.get_execution_history("nonexistent")
            assert exc_info.value.status_code == 404


class TestClientStubs:
    """Verify the client raises AgenticPlatformError for 501 stub endpoints."""

    @pytest.mark.asyncio
    async def test_identity_control_plane_stub(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.create_credential_provider("test", "oauth2")
            assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_browser_stub(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.start_browser_session()
            assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_gateway_stub(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.completions({"model": "test"})
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
