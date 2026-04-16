"""Tests for the primitive helper classes (Memory, Browser, CodeInterpreter, etc.)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_primitives_gateway_client import AgenticPlatformClient


def _mock_client() -> AgenticPlatformClient:
    """Create a mock AgenticPlatformClient with async method stubs."""
    client = MagicMock(spec=AgenticPlatformClient)
    # Memory methods
    client.store_memory = AsyncMock(return_value={"namespace": "ns", "key": "k1", "content": "c"})
    client.retrieve_memory = AsyncMock(
        return_value={"namespace": "ns", "key": "k1", "content": "hello", "metadata": {}}
    )
    client.search_memory = AsyncMock(
        return_value={"results": [{"record": {"key": "k1", "content": "found"}, "score": 0.9}]}
    )
    client.list_memories = AsyncMock(return_value={"records": [{"key": "turn-1", "content": "[user] hi"}], "total": 1})
    client.delete_memory = AsyncMock(return_value=None)
    client.list_memory_sessions = AsyncMock(return_value={"sessions": []})
    # Observability methods
    client.ingest_trace = AsyncMock(return_value={"status": "accepted"})
    client.ingest_log = AsyncMock(return_value={"status": "accepted"})
    client.query_traces = AsyncMock(return_value={"traces": []})
    client.get_trace = AsyncMock(return_value={"trace_id": "t1"})
    client.update_trace = AsyncMock(return_value={"trace_id": "t1", "status": "updated"})
    client.flush_observability = AsyncMock(return_value={"status": "accepted"})
    client.log_generation = AsyncMock(return_value={"trace_id": "t1", "name": "gen"})
    client.score_trace = AsyncMock(return_value={"trace_id": "t1", "name": "accuracy", "value": 0.9})
    # Browser methods
    client.start_browser_session = AsyncMock(return_value={"session_id": "b1", "status": "active"})
    client.stop_browser_session = AsyncMock(return_value=None)
    client.browser_navigate = AsyncMock(return_value={"url": "https://example.com", "title": "Example"})
    client.browser_screenshot = AsyncMock(return_value={"format": "png", "data": "base64data"})
    client.browser_get_content = AsyncMock(return_value={"content": "<html>page</html>"})
    client.browser_click = AsyncMock(return_value={"status": "clicked"})
    client.browser_type = AsyncMock(return_value={"status": "typed"})
    client.browser_evaluate = AsyncMock(return_value={"result": "hello"})
    client.get_live_view_url = AsyncMock(return_value={"url": "https://live.example.com"})
    # Code interpreter methods
    client.start_code_session = AsyncMock(return_value={"session_id": "c1", "status": "active"})
    client.stop_code_session = AsyncMock(return_value=None)
    client.execute_code = AsyncMock(return_value={"stdout": "4\n", "stderr": "", "error": ""})
    client.get_code_session = AsyncMock(return_value={"session_id": "c1", "status": "active"})
    # Identity methods
    client.get_token = AsyncMock(return_value={"access_token": "tok", "token_type": "Bearer"})
    client.get_api_key = AsyncMock(return_value={"api_key": "key123"})
    client.get_workload_token = AsyncMock(return_value={"workload_token": "wl-tok-abcdef1234567890"})
    return client


# ── Memory ────────────────────────────────────────────────────────────


class TestMemory:
    @pytest.fixture
    def memory(self):
        from agentic_primitives_gateway_client.primitives.memory import Memory

        return Memory(_mock_client(), namespace="test-ns", session_id="s1")

    @pytest.mark.asyncio
    async def test_store_turn(self, memory):
        await memory.store_turn("user", "hello")
        memory._client.store_memory.assert_called_once()
        args, kwargs = memory._client.store_memory.call_args
        assert args[0] == "test-ns"
        # content is the 3rd positional arg or in kwargs
        content = args[2] if len(args) > 2 else kwargs.get("content", "")
        assert "[user] hello" in content

    @pytest.mark.asyncio
    async def test_store_turn_error_suppressed(self, memory):
        memory._client.store_memory = AsyncMock(side_effect=Exception("fail"))
        await memory.store_turn("user", "hello")  # should not raise

    @pytest.mark.asyncio
    async def test_recall_context(self, memory):
        context = await memory.recall_context("hello")
        assert "Recent conversation" in context
        assert "Related memories" in context

    @pytest.mark.asyncio
    async def test_recall_context_empty(self, memory):
        memory._client.list_memories = AsyncMock(return_value={"records": []})
        memory._client.search_memory = AsyncMock(return_value={"results": []})
        context = await memory.recall_context("hello")
        assert context == ""

    @pytest.mark.asyncio
    async def test_recall_context_error_suppressed(self, memory):
        memory._client.list_memories = AsyncMock(side_effect=Exception("fail"))
        memory._client.search_memory = AsyncMock(side_effect=Exception("fail"))
        context = await memory.recall_context("hello")
        assert context == ""

    @pytest.mark.asyncio
    async def test_remember(self, memory):
        result = await memory.remember("key1", "content1", source="docs")
        assert "Stored" in result
        memory._client.store_memory.assert_called_once()

    @pytest.mark.asyncio
    async def test_recall(self, memory):
        result = await memory.recall("key1")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_recall_not_found(self, memory):
        memory._client.retrieve_memory = AsyncMock(side_effect=Exception("404"))
        result = await memory.recall("missing")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_search(self, memory):
        result = await memory.search("query")
        assert "found" in result

    @pytest.mark.asyncio
    async def test_search_no_results(self, memory):
        memory._client.search_memory = AsyncMock(return_value={"results": []})
        result = await memory.search("query")
        assert "No relevant" in result

    @pytest.mark.asyncio
    async def test_list(self, memory):
        result = await memory.list()
        assert "[user] hi" in result

    @pytest.mark.asyncio
    async def test_list_empty(self, memory):
        memory._client.list_memories = AsyncMock(return_value={"records": []})
        result = await memory.list()
        assert "No memories" in result

    @pytest.mark.asyncio
    async def test_forget(self, memory):
        result = await memory.forget("key1")
        assert "Deleted" in result
        memory._client.delete_memory.assert_called_once()

    @pytest.mark.asyncio
    async def test_forget_not_found(self, memory):
        memory._client.delete_memory = AsyncMock(side_effect=Exception("404"))
        result = await memory.forget("missing")
        assert "could not delete" in result.lower() or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_list_conversations(self, memory):
        result = await memory.list_conversations()
        assert "No conversations" in result or "sessions" in result.lower()

    @pytest.mark.asyncio
    async def test_with_observability(self):
        from agentic_primitives_gateway_client.primitives.memory import Memory

        obs = MagicMock()
        obs.trace = AsyncMock()
        mem = Memory(_mock_client(), namespace="ns", observability=obs)
        await mem.remember("k", "v")
        obs.trace.assert_called_once()


# ── Browser ───────────────────────────────────────────────────────────


class TestBrowser:
    @pytest.fixture
    def browser(self):
        from agentic_primitives_gateway_client.primitives.browser import Browser

        return Browser(_mock_client())

    @pytest.mark.asyncio
    async def test_start(self, browser):
        result = await browser.start()
        assert "session" in result.lower() or "b1" in result
        assert browser.session_id == "b1"

    @pytest.mark.asyncio
    async def test_close(self, browser):
        await browser.start()
        result = await browser.close()
        assert "stopped" in result.lower()
        assert browser.session_id is None

    @pytest.mark.asyncio
    async def test_close_no_session(self, browser):
        result = await browser.close()
        assert "no" in result.lower() and "session" in result.lower()

    @pytest.mark.asyncio
    async def test_navigate(self, browser):
        await browser.start()
        result = await browser.navigate("https://example.com")
        assert "Navigated" in result

    @pytest.mark.asyncio
    async def test_get_page_content(self, browser):
        await browser.start()
        result = await browser.get_page_content()
        assert "html" in result.lower() or "page" in result.lower()

    @pytest.mark.asyncio
    async def test_click(self, browser):
        await browser.start()
        result = await browser.click("button#submit")
        assert "Clicked" in result

    @pytest.mark.asyncio
    async def test_type_text(self, browser):
        await browser.start()
        result = await browser.type_text("input#name", "hello")
        assert "Typed" in result

    @pytest.mark.asyncio
    async def test_screenshot(self, browser):
        await browser.start()
        result = await browser.screenshot()
        assert "Screenshot" in result or "bytes" in result

    @pytest.mark.asyncio
    async def test_evaluate(self, browser):
        await browser.start()
        result = await browser.evaluate("document.title")
        assert isinstance(result, str)


# ── CodeInterpreter ───────────────────────────────────────────────────


class TestCodeInterpreter:
    @pytest.fixture
    def code(self):
        from agentic_primitives_gateway_client.primitives.code_interpreter import CodeInterpreter

        return CodeInterpreter(_mock_client())

    @pytest.mark.asyncio
    async def test_execute_starts_session(self, code):
        result = await code.execute("print(2+2)")
        assert "4" in result
        assert code.session_id == "c1"

    @pytest.mark.asyncio
    async def test_execute_reuses_session(self, code):
        await code.execute("print(1)")
        await code.execute("print(2)")
        code._client.start_code_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_close(self, code):
        await code.execute("x = 1")  # starts session
        await code.close()
        assert code.session_id is None
        code._client.stop_code_session.assert_called_once_with("c1")

    @pytest.mark.asyncio
    async def test_close_no_session(self, code):
        await code.close()  # should not raise
        code._client.stop_code_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_session(self, code):
        await code.execute("x = 1")
        result = await code.get_session()
        assert result["session_id"] == "c1"

    @pytest.mark.asyncio
    async def test_get_session_no_active(self, code):
        result = await code.get_session()
        assert "error" in result


# ── Observability ─────────────────────────────────────────────────────


class TestObservability:
    @pytest.fixture
    def obs(self):
        from agentic_primitives_gateway_client.primitives.observability import Observability

        return Observability(_mock_client(), namespace="test")

    @pytest.mark.asyncio
    async def test_trace(self, obs):
        await obs.trace("event", {"key": "val"}, "output")
        obs._client.ingest_trace.assert_called_once()

    @pytest.mark.asyncio
    async def test_trace_error_suppressed(self, obs):
        obs._client.ingest_trace = AsyncMock(side_effect=Exception("fail"))
        await obs.trace("event", {}, "out")  # should not raise

    @pytest.mark.asyncio
    async def test_log(self, obs):
        await obs.log("info", "test message")
        obs._client.ingest_log.assert_called_once()

    @pytest.mark.asyncio
    async def test_flush(self, obs):
        await obs.flush()
        obs._client.flush_observability.assert_called_once()

    def test_trace_sync(self, obs):
        mock_http = MagicMock()
        obs._sync_http = mock_http
        obs.trace_sync("event", {}, "out")
        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        assert call_args[0][0] == "/api/v1/observability/traces"

    def test_log_sync(self, obs):
        mock_http = MagicMock()
        obs._sync_http = mock_http
        obs.log_sync("info", "msg")
        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        assert call_args[0][0] == "/api/v1/observability/logs"


# ── Identity ──────────────────────────────────────────────────────────


class TestIdentity:
    @pytest.fixture
    def identity(self):
        from agentic_primitives_gateway_client.primitives.identity import Identity

        return Identity(_mock_client())

    @pytest.mark.asyncio
    async def test_get_token(self, identity):
        result = await identity.get_token("github", "wl-token")
        assert "tok" in str(result)

    @pytest.mark.asyncio
    async def test_get_api_key(self, identity):
        result = await identity.get_api_key("service", "wl-token")
        assert "key123" in str(result)

    @pytest.mark.asyncio
    async def test_get_workload_token(self, identity):
        result = await identity.get_workload_token("agent1")
        assert "workload token" in result.lower()
