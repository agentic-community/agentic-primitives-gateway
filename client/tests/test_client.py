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


class TestClientAgents:
    @pytest.mark.asyncio
    async def test_create_agent(self, make_client) -> None:
        async with make_client() as client:
            result = await client.create_agent({"name": "my-agent", "model": "claude-3"})
            assert result["name"] == "my-agent"
            assert result["model"] == "claude-3"

    @pytest.mark.asyncio
    async def test_create_agent_conflict(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "my-agent", "model": "claude-3"})
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.create_agent({"name": "my-agent", "model": "claude-3"})
            assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_list_agents(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "agent-1", "model": "claude-3"})
            await client.create_agent({"name": "agent-2", "model": "gpt-4"})
            result = await client.list_agents()
            assert len(result["agents"]) == 2

    @pytest.mark.asyncio
    async def test_list_agents_empty(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_agents()
            assert result["agents"] == []

    @pytest.mark.asyncio
    async def test_get_agent(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "my-agent", "model": "claude-3"})
            result = await client.get_agent("my-agent")
            assert result["name"] == "my-agent"
            assert result["model"] == "claude-3"

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.get_agent("nonexistent")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_agent(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "my-agent", "model": "claude-3"})
            result = await client.update_agent("my-agent", {"description": "updated"})
            assert result["name"] == "my-agent"
            assert result["description"] == "updated"

    @pytest.mark.asyncio
    async def test_update_agent_not_found(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.update_agent("nonexistent", {"description": "nope"})
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_agent(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "my-agent", "model": "claude-3"})
            result = await client.delete_agent("my-agent")
            assert result["status"] == "deleted"
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.get_agent("my-agent")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_agent_not_found(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.delete_agent("nonexistent")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_chat_with_agent(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "my-agent", "model": "claude-3"})
            result = await client.chat_with_agent("my-agent", "Hello!")
            assert result["agent_name"] == "my-agent"
            assert "Hello!" in result["response"]
            assert "session_id" in result
            assert result["turns_used"] == 1

    @pytest.mark.asyncio
    async def test_chat_with_agent_session_id(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "my-agent", "model": "claude-3"})
            result = await client.chat_with_agent("my-agent", "Hi", session_id="sess-42")
            assert result["session_id"] == "sess-42"

    @pytest.mark.asyncio
    async def test_chat_with_agent_not_found(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.chat_with_agent("nonexistent", "Hello")
            assert exc_info.value.status_code == 404


class TestClientAgentVersions:
    @pytest.mark.asyncio
    async def test_initial_create_has_v1(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "r", "model": "m"})
            versions = await client.list_agent_versions("r")
            assert len(versions["versions"]) == 1
            assert versions["versions"][0]["version_number"] == 1

    @pytest.mark.asyncio
    async def test_create_version_bumps_number(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "r", "model": "m"})
            v2 = await client.create_agent_version("r", {"description": "v2", "commit_message": "tweak"})
            assert v2["version_number"] == 2
            versions = await client.list_agent_versions("r")
            assert [v["version_number"] for v in versions["versions"]] == [1, 2]

    @pytest.mark.asyncio
    async def test_deploy_version(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "r", "model": "m"})
            v2 = await client.create_agent_version("r", {"description": "v2"})
            # Propose + approve + deploy round-trip exercises the full flow.
            await client.propose_agent_version("r", v2["version_id"])
            await client.approve_agent_version("r", v2["version_id"])
            result = await client.deploy_agent_version("r", v2["version_id"])
            assert result["status"] == "deployed"

    @pytest.mark.asyncio
    async def test_reject_version(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "r", "model": "m"})
            v2 = await client.create_agent_version("r", {"description": "v2"})
            await client.propose_agent_version("r", v2["version_id"])
            result = await client.reject_agent_version("r", v2["version_id"], reason="nope")
            assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_fork_agent(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "source", "model": "m"})
            fork = await client.fork_agent("source", target_name="my-fork")
            assert fork["agent_name"] == "my-fork"
            assert fork["forked_from"] is not None
            assert fork["forked_from"]["name"] == "source"

    @pytest.mark.asyncio
    async def test_get_lineage(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "r", "model": "m"})
            lineage = await client.get_agent_lineage("r")
            assert lineage["root_identity"]["name"] == "r"
            assert len(lineage["nodes"]) >= 1

    @pytest.mark.asyncio
    async def test_pending_proposals(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "r", "model": "m"})
            v2 = await client.create_agent_version("r", {"description": "v2"})
            await client.propose_agent_version("r", v2["version_id"])
            pending = await client.list_pending_agent_proposals()
            version_ids = {v["version_id"] for v in pending["versions"]}
            assert v2["version_id"] in version_ids


class TestClientAgentSessions:
    @pytest.mark.asyncio
    async def test_list_sessions(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "sess-agent", "model": "m"})
            result = await client.list_agent_sessions("sess-agent")
            assert "sessions" in result

    @pytest.mark.asyncio
    async def test_get_session_history(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "hist-agent", "model": "m"})
            result = await client.get_session_history("hist-agent", "s1")
            assert result["agent_name"] == "hist-agent"
            assert result["session_id"] == "s1"
            assert isinstance(result["messages"], list)

    @pytest.mark.asyncio
    async def test_get_session_status(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "stat-agent", "model": "m"})
            result = await client.get_session_status("stat-agent", "s1")
            assert result["status"] == "idle"

    @pytest.mark.asyncio
    async def test_delete_session(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "del-agent", "model": "m"})
            await client.delete_session("del-agent", "s1")  # should not raise

    @pytest.mark.asyncio
    async def test_get_agent_tools(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "tool-agent", "model": "m", "primitives": {"memory": {"enabled": True}}})
            result = await client.get_agent_tools("tool-agent")
            assert result["agent_name"] == "tool-agent"
            assert len(result["tools"]) > 0

    @pytest.mark.asyncio
    async def test_get_agent_memory(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent(
                {
                    "name": "mem-agent",
                    "model": "m",
                    "primitives": {"memory": {"enabled": True, "namespace": "agent:{agent_name}"}},
                }
            )
            result = await client.get_agent_memory("mem-agent")
            assert result["agent_name"] == "mem-agent"
            assert result["memory_enabled"] is True


class TestClientTeams:
    @pytest.mark.asyncio
    async def test_create_team(self, make_client) -> None:
        async with make_client() as client:
            result = await client.create_team({"name": "t1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            assert result["name"] == "t1"

    @pytest.mark.asyncio
    async def test_create_team_conflict(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "dup", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.create_team({"name": "dup", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_list_teams(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "lt1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            result = await client.list_teams()
            assert len(result["teams"]) >= 1

    @pytest.mark.asyncio
    async def test_get_team(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "gt1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            result = await client.get_team("gt1")
            assert result["name"] == "gt1"

    @pytest.mark.asyncio
    async def test_get_team_not_found(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.get_team("nonexistent")
            assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_team(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "ut1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            result = await client.update_team("ut1", {"description": "updated"})
            assert result["description"] == "updated"

    @pytest.mark.asyncio
    async def test_delete_team(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "dt1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            result = await client.delete_team("dt1")
            assert result["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_delete_team_not_found(self, make_client) -> None:
        async with make_client() as client:
            with pytest.raises(AgenticPlatformError) as exc_info:
                await client.delete_team("nonexistent")
            assert exc_info.value.status_code == 404


class TestClientTeamRuns:
    @pytest.mark.asyncio
    async def test_list_team_runs(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "lr1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            result = await client.list_team_runs("lr1")
            assert "runs" in result

    @pytest.mark.asyncio
    async def test_get_team_run_status(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "sr1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            result = await client.get_team_run_status("sr1", "nonexistent-run")
            assert result["status"] == "idle"

    @pytest.mark.asyncio
    async def test_get_team_run_events(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "er1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            result = await client.get_team_run_events("er1", "nonexistent-run")
            assert result["status"] == "unknown"
            assert result["events"] == []

    @pytest.mark.asyncio
    async def test_get_team_run(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "tr1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            result = await client.get_team_run("tr1", "nonexistent-run")
            assert result["tasks"] == []

    @pytest.mark.asyncio
    async def test_delete_team_run(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "dr1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            result = await client.delete_team_run("dr1", "nonexistent-run")
            assert result["status"] == "deleted"


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
    async def test_llm_stub(self, make_client) -> None:
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
        client._auth_headers = {}
        client._aws_headers = {}
        client._aws_from_environment = False
        client._provider_headers = {}
        client._service_cred_headers = {}
        client._agent_id_header = {}
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
        client._auth_headers = {}
        client._aws_headers = {"x-aws-access-key-id": "AKIA"}
        client._aws_from_environment = False
        client._provider_headers = {}
        client._service_cred_headers = {}
        client._agent_id_header = {}
        client.clear_aws_credentials()
        assert client._headers == {}

    def test_no_credentials_returns_empty_headers(self) -> None:
        client = AgenticPlatformClient.__new__(AgenticPlatformClient)
        client._auth_headers = {}
        client._aws_headers = {}
        client._aws_from_environment = False
        client._provider_headers = {}
        client._service_cred_headers = {}
        client._agent_id_header = {}
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
        client._auth_headers = {}
        client._aws_headers = {}
        client._aws_from_environment = True
        client._aws_profile = None
        client._aws_env_region = None
        client._provider_headers = {}
        client._service_cred_headers = {}
        client._agent_id_header = {}

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
        client._auth_headers = {}
        client._aws_headers = {}
        client._aws_from_environment = True
        client._aws_profile = None
        client._aws_env_region = "eu-west-1"  # explicit override
        client._provider_headers = {}
        client._service_cred_headers = {}
        client._agent_id_header = {}

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
        client._agent_id_header = {}

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


class TestClientPolicy:
    @pytest.mark.asyncio
    async def test_create_and_get_engine(self, make_client) -> None:
        async with make_client() as client:
            result = await client.create_policy_engine(name="test-engine")
            assert result["name"] == "test-engine"
            engine_id = result["policy_engine_id"]
            info = await client.get_policy_engine(engine_id)
            assert info["policy_engine_id"] == engine_id

    @pytest.mark.asyncio
    async def test_list_engines(self, make_client) -> None:
        async with make_client() as client:
            await client.create_policy_engine(name="eng-1")
            result = await client.list_policy_engines()
            assert len(result["policy_engines"]) == 1

    @pytest.mark.asyncio
    async def test_delete_engine(self, make_client) -> None:
        async with make_client() as client:
            result = await client.create_policy_engine(name="to-delete")
            await client.delete_policy_engine(result["policy_engine_id"])

    @pytest.mark.asyncio
    async def test_policy_crud(self, make_client) -> None:
        async with make_client() as client:
            engine = await client.create_policy_engine(name="eng-for-policies")
            eid = engine["policy_engine_id"]
            pol = await client.create_policy(eid, policy_body="permit(...);")
            pid = pol["policy_id"]
            info = await client.get_policy(eid, pid)
            assert info["policy_id"] == pid
            listed = await client.list_policies(eid)
            assert len(listed["policies"]) == 1
            await client.delete_policy(eid, pid)


class TestClientEvaluations:
    @pytest.mark.asyncio
    async def test_create_and_get_evaluator(self, make_client) -> None:
        async with make_client() as client:
            result = await client.create_evaluator(name="test-eval", evaluator_type="custom")
            assert result["name"] == "test-eval"
            eid = result["evaluator_id"]
            info = await client.get_evaluator(eid)
            assert info["evaluator_id"] == eid

    @pytest.mark.asyncio
    async def test_list_evaluators(self, make_client) -> None:
        async with make_client() as client:
            await client.create_evaluator(name="eval-1", evaluator_type="custom")
            result = await client.list_evaluators()
            assert len(result["evaluators"]) == 1

    @pytest.mark.asyncio
    async def test_delete_evaluator(self, make_client) -> None:
        async with make_client() as client:
            result = await client.create_evaluator(name="to-del", evaluator_type="custom")
            await client.delete_evaluator(result["evaluator_id"])

    @pytest.mark.asyncio
    async def test_evaluate(self, make_client) -> None:
        async with make_client() as client:
            result = await client.evaluate(
                evaluator_id="Builtin.Helpfulness",
                input_data="What is Python?",
                output_data="Python is a programming language.",
            )
            assert "evaluation_results" in result


class TestAgentId:
    def _make_client(self) -> AgenticPlatformClient:
        client = AgenticPlatformClient.__new__(AgenticPlatformClient)
        client._auth_headers = {}
        client._aws_headers = {}
        client._aws_from_environment = False
        client._provider_headers = {}
        client._service_cred_headers = {}
        client._agent_id_header = {}
        return client

    def test_set_agent_id(self) -> None:
        client = self._make_client()
        client.set_agent_id("my-agent")
        assert client._headers["x-agent-id"] == "my-agent"

    def test_clear_agent_id(self) -> None:
        client = self._make_client()
        client.set_agent_id("my-agent")
        client.clear_agent_id()
        assert "x-agent-id" not in client._headers

    def test_agent_id_in_constructor(self) -> None:
        """agent_id constructor param sets the header."""
        client = self._make_client()
        client._agent_id_header = {}
        client.set_agent_id("init-agent")
        assert client._headers["x-agent-id"] == "init-agent"

    def test_agent_id_coexists_with_aws_headers(self) -> None:
        client = self._make_client()
        client.set_aws_credentials(
            access_key_id="AKIA_TEST",
            secret_access_key="SECRET",
        )
        client.set_agent_id("my-agent")
        headers = client._headers
        assert headers["x-agent-id"] == "my-agent"
        assert headers["x-aws-access-key-id"] == "AKIA_TEST"


class TestAuth:
    def _make_client(self) -> AgenticPlatformClient:
        client = AgenticPlatformClient.__new__(AgenticPlatformClient)
        client._auth_headers = {}
        client._aws_headers = {}
        client._aws_from_environment = False
        client._provider_headers = {}
        client._service_cred_headers = {}
        client._agent_id_header = {}
        return client

    def test_set_auth_token(self) -> None:
        client = self._make_client()
        client.set_auth_token("eyJhbGciOiJSUzI1NiIs...")
        assert client._headers["authorization"] == "Bearer eyJhbGciOiJSUzI1NiIs..."

    def test_set_api_key(self) -> None:
        client = self._make_client()
        client.set_api_key("sk-dev-12345")
        assert client._headers["x-api-key"] == "sk-dev-12345"

    def test_clear_auth(self) -> None:
        client = self._make_client()
        client.set_auth_token("token")
        client.clear_auth()
        assert "authorization" not in client._headers
        assert "x-api-key" not in client._headers

    def test_api_key_replaces_token(self) -> None:
        client = self._make_client()
        client.set_auth_token("token")
        client.set_api_key("sk-key")
        assert "authorization" not in client._headers
        assert client._headers["x-api-key"] == "sk-key"

    def test_token_replaces_api_key(self) -> None:
        client = self._make_client()
        client.set_api_key("sk-key")
        client.set_auth_token("token")
        assert client._headers["authorization"] == "Bearer token"
        assert "x-api-key" not in client._headers

    def test_auth_coexists_with_aws_headers(self) -> None:
        client = self._make_client()
        client.set_auth_token("jwt-token")
        client.set_aws_credentials(access_key_id="AKIA", secret_access_key="SECRET")
        headers = client._headers
        assert headers["authorization"] == "Bearer jwt-token"
        assert headers["x-aws-access-key-id"] == "AKIA"


class TestMissingMethods:
    """Tests for client methods added after the initial release."""

    @pytest.mark.asyncio
    async def test_get_auth_config(self, make_client) -> None:
        async with make_client() as client:
            result = await client.get_auth_config()
            assert result["backend"] == "noop"

    @pytest.mark.asyncio
    async def test_whoami(self, make_client) -> None:
        async with make_client() as client:
            result = await client.whoami()
            assert result["id"] == "noop"
            assert result["is_admin"] is True

    @pytest.mark.asyncio
    async def test_audit_status(self, make_client) -> None:
        async with make_client() as client:
            result = await client.audit_status()
            assert result["stream_sink_configured"] is True
            assert result["stream_name"] == "gateway:audit"
            assert result["length"] == 3

    @pytest.mark.asyncio
    async def test_list_audit_events(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_audit_events(count=10, outcome="success")
            assert result["events"][0]["action"] == "auth.success"
            assert result["next"] is None

    @pytest.mark.asyncio
    async def test_list_providers(self, make_client) -> None:
        async with make_client() as client:
            result = await client.list_providers()
            assert "memory" in result

    @pytest.mark.asyncio
    async def test_list_memory_namespaces(self, make_client) -> None:
        async with make_client() as client:
            await client.store_memory("ns1", "k1", "content")
            result = await client.list_memory_namespaces()
            assert "namespaces" in result

    @pytest.mark.asyncio
    async def test_get_tool_catalog(self, make_client) -> None:
        async with make_client() as client:
            result = await client.get_tool_catalog()
            assert "memory" in result

    @pytest.mark.asyncio
    async def test_cancel_session_run(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "a1", "model": "test"})
            result = await client.cancel_session_run("a1", "session-1")
            assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_cleanup_sessions(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "a1", "model": "test"})
            result = await client.cleanup_sessions("a1", keep=3)
            assert "deleted_count" in result

    @pytest.mark.asyncio
    async def test_cancel_team_run(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "t1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            result = await client.cancel_team_run("t1", "run-1")
            assert result["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_reconnect_session_stream(self, make_client) -> None:
        async with make_client() as client:
            await client.create_agent({"name": "a1", "model": "test"})
            resp = await client.reconnect_session_stream("a1", "session-1")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_reconnect_team_stream(self, make_client) -> None:
        async with make_client() as client:
            await client.create_team({"name": "t1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            resp = await client.reconnect_team_stream("t1", "run-1")
            assert resp.status_code == 200


class TestA2AClient:
    """Tests for A2A protocol client methods."""

    @pytest.mark.asyncio
    async def test_get_agent_card(self, make_client) -> None:
        async with make_client() as client:
            card = await client.a2a_get_agent_card()
            assert card["name"] == "Test Gateway"
            assert len(card["skills"]) == 1
            assert card["skills"][0]["id"] == "test-agent"
            assert card["capabilities"]["streaming"] is True

    @pytest.mark.asyncio
    async def test_get_per_agent_card(self, make_client) -> None:
        async with make_client() as client:
            card = await client.a2a_get_per_agent_card("researcher")
            assert card["name"] == "researcher"
            assert card["skills"][0]["id"] == "researcher"

    @pytest.mark.asyncio
    async def test_send_message(self, make_client) -> None:
        async with make_client() as client:
            task = await client.a2a_send_message("test-agent", "Hello, what can you do?")
            assert task["status"]["state"] == "completed"
            assert task["artifacts"][0]["parts"][0]["text"] == "Mock A2A response"

    @pytest.mark.asyncio
    async def test_send_message_with_task_id(self, make_client) -> None:
        async with make_client() as client:
            task = await client.a2a_send_message("test-agent", "Hello", task_id="custom-task-id")
            assert task["id"] == "custom-task-id"

    @pytest.mark.asyncio
    async def test_send_message_stream(self, make_client) -> None:
        async with make_client() as client:
            resp = await client.a2a_send_message_stream("test-agent", "Hello")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_task(self, make_client) -> None:
        async with make_client() as client:
            task = await client.a2a_get_task("test-agent", "task-123")
            assert task["id"] == "task-123"
            assert task["status"]["state"] == "completed"

    @pytest.mark.asyncio
    async def test_cancel_task(self, make_client) -> None:
        async with make_client() as client:
            task = await client.a2a_cancel_task("test-agent", "task-123")
            assert task["id"] == "task-123"
            assert task["status"]["state"] == "canceled"

    @pytest.mark.asyncio
    async def test_subscribe_task(self, make_client) -> None:
        async with make_client() as client:
            resp = await client.a2a_subscribe_task("test-agent", "task-123")
            assert resp.status_code == 200


class TestAuthConstructor:
    def _make_client(self) -> AgenticPlatformClient:
        client = AgenticPlatformClient.__new__(AgenticPlatformClient)
        client._auth_headers = {}
        client._aws_headers = {}
        client._aws_from_environment = False
        client._provider_headers = {}
        client._service_cred_headers = {}
        client._agent_id_header = {}
        return client

    def test_auth_token_in_constructor(self) -> None:
        client = AgenticPlatformClient(
            "http://localhost:8000",
            auth_token="my-jwt",
        )
        assert client._headers["authorization"] == "Bearer my-jwt"

    def test_api_key_in_constructor(self) -> None:
        client = AgenticPlatformClient(
            "http://localhost:8000",
            api_key="sk-test",
        )
        assert client._headers["x-api-key"] == "sk-test"

    def test_no_auth_empty_headers(self) -> None:
        client = self._make_client()
        assert "authorization" not in client._headers
        assert "x-api-key" not in client._headers
