"""Tests for the teams subsystem: team store, team routes, and team runner."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agentic_primitives_gateway.agents.file_store import FileAgentStore, FileTeamStore
from agentic_primitives_gateway.agents.team_runner import TeamRunner
from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.models.agents import AgentSpec
from agentic_primitives_gateway.models.teams import TeamSpec
from agentic_primitives_gateway.primitives.llm.base import LLMProvider
from agentic_primitives_gateway.primitives.tasks.in_memory import InMemoryTasksProvider
from agentic_primitives_gateway.routes.agents import set_agent_store
from agentic_primitives_gateway.routes.teams import set_team_store

# ── Mock gateway ─────────────────────────────────────────────────────


class MockLLMProvider(LLMProvider):
    def __init__(self) -> None:
        self._responses: list[dict[str, Any]] = []
        self._call_index = 0

    def set_responses(self, responses: list[dict[str, Any]]) -> None:
        self._responses = responses
        self._call_index = 0

    async def route_request(self, model_request: dict[str, Any]) -> dict[str, Any]:
        if self._call_index < len(self._responses):
            resp = self._responses[self._call_index]
            self._call_index += 1
            return resp
        return {"model": "mock", "content": "default response", "stop_reason": "end_turn", "usage": {}}

    async def list_models(self) -> list[dict[str, Any]]:
        return []

    async def healthcheck(self) -> bool:
        return True


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def agent_store(tmp_path: Any) -> FileAgentStore:
    store = FileAgentStore(path=str(tmp_path / "agents.json"))
    set_agent_store(store)
    return store


@pytest.fixture
def team_store(tmp_path: Any) -> FileTeamStore:
    store = FileTeamStore(path=str(tmp_path / "teams.json"))
    set_team_store(store)
    return store


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ── Team store tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_store_crud(tmp_path: Any) -> None:
    store = FileTeamStore(path=str(tmp_path / "teams.json"))
    spec = TeamSpec(name="test-team", planner="p", synthesizer="s", workers=["w1", "w2"])

    created = await store.create(spec)
    assert created.name == "test-team"

    fetched = await store.get("test-team")
    assert fetched is not None
    assert fetched.workers == ["w1", "w2"]

    teams = await store.list()
    assert len(teams) == 1

    updated = await store.update("test-team", {"description": "Updated"})
    assert updated.description == "Updated"

    deleted = await store.delete("test-team")
    assert deleted is True
    assert await store.get("test-team") is None


@pytest.mark.asyncio
async def test_team_store_seed(tmp_path: Any) -> None:
    store = FileTeamStore(path=str(tmp_path / "teams.json"))
    await store.seed_async(
        {
            "my-team": {
                "planner": "planner-agent",
                "synthesizer": "synth-agent",
                "workers": ["w1"],
            }
        }
    )
    teams = await store.list()
    assert len(teams) == 1
    assert teams[0].name == "my-team"


@pytest.mark.asyncio
async def test_team_store_persistence(tmp_path: Any) -> None:
    path = str(tmp_path / "teams.json")
    store1 = FileTeamStore(path=path)
    await store1.create(TeamSpec(name="t1", planner="p", synthesizer="s", workers=["w"]))

    store2 = FileTeamStore(path=path)
    assert await store2.get("t1") is not None


# ── Team routes tests ────────────────────────────────────────────────


def test_create_team(client: TestClient, team_store: FileTeamStore) -> None:
    resp = client.post(
        "/api/v1/teams",
        json={
            "name": "route-team",
            "planner": "planner",
            "synthesizer": "synthesizer",
            "workers": ["w1", "w2"],
        },
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "route-team"


def test_create_team_duplicate(client: TestClient, team_store: FileTeamStore) -> None:
    client.post(
        "/api/v1/teams",
        json={
            "name": "dupe",
            "planner": "p",
            "synthesizer": "s",
            "workers": ["w"],
        },
    )
    resp = client.post(
        "/api/v1/teams",
        json={
            "name": "dupe",
            "planner": "p",
            "synthesizer": "s",
            "workers": ["w"],
        },
    )
    assert resp.status_code == 409


def test_list_teams(client: TestClient, team_store: FileTeamStore) -> None:
    client.post(
        "/api/v1/teams",
        json={
            "name": "t1",
            "planner": "p",
            "synthesizer": "s",
            "workers": ["w"],
        },
    )
    resp = client.get("/api/v1/teams")
    assert resp.status_code == 200
    assert len(resp.json()["teams"]) >= 1


def test_get_team(client: TestClient, team_store: FileTeamStore) -> None:
    client.post(
        "/api/v1/teams",
        json={
            "name": "t2",
            "planner": "p",
            "synthesizer": "s",
            "workers": ["w"],
        },
    )
    resp = client.get("/api/v1/teams/t2")
    assert resp.status_code == 200
    assert resp.json()["name"] == "t2"


def test_get_team_not_found(client: TestClient, team_store: FileTeamStore) -> None:
    resp = client.get("/api/v1/teams/nonexistent")
    assert resp.status_code == 404


def test_update_team(client: TestClient, team_store: FileTeamStore) -> None:
    client.post(
        "/api/v1/teams",
        json={
            "name": "t3",
            "planner": "p",
            "synthesizer": "s",
            "workers": ["w"],
        },
    )
    resp = client.put("/api/v1/teams/t3", json={"description": "Updated desc"})
    assert resp.status_code == 200
    assert resp.json()["description"] == "Updated desc"


def test_delete_team(client: TestClient, team_store: FileTeamStore) -> None:
    client.post(
        "/api/v1/teams",
        json={
            "name": "t4",
            "planner": "p",
            "synthesizer": "s",
            "workers": ["w"],
        },
    )
    resp = client.delete("/api/v1/teams/t4")
    assert resp.status_code == 200


# ── Team runner tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_runner_full_run(tmp_path: Any) -> None:
    """Test the full team run lifecycle with mocked LLM."""
    # Set up stores
    agent_store = FileAgentStore(path=str(tmp_path / "agents.json"))
    team_store = FileTeamStore(path=str(tmp_path / "teams.json"))
    tasks_provider = InMemoryTasksProvider()

    # Create agents
    planner = AgentSpec(name="planner", model="mock", system_prompt="Plan tasks")
    researcher = AgentSpec(name="researcher", model="mock", system_prompt="Research things")
    synthesizer = AgentSpec(name="synthesizer", model="mock", system_prompt="Synthesize")
    await agent_store.create(planner)
    await agent_store.create(researcher)
    await agent_store.create(synthesizer)

    # Create team
    team_spec = TeamSpec(
        name="test-team",
        planner="planner",
        synthesizer="synthesizer",
        workers=["researcher"],
    )

    # Mock gateway responses:
    # 1. Planner: creates a task via tool call, then ends
    # 2. Worker: completes the task
    # 3. Synthesizer: produces final response
    mock_gw = MockLLMProvider()
    mock_gw.set_responses(
        [
            # Planner turn 1: call create_task
            {
                "model": "mock",
                "content": "Let me create a task.",
                "stop_reason": "tool_use",
                "tool_calls": [
                    {
                        "id": "tc1",
                        "name": "create_task",
                        "input": {"title": "Research the topic", "description": "Find information"},
                    }
                ],
                "usage": {},
            },
            # Planner turn 2: done planning
            {"model": "mock", "content": "Tasks created.", "stop_reason": "end_turn", "usage": {}},
            # Worker turn 1: just responds (the task board tools are available but worker may not use them)
            {"model": "mock", "content": "Here is my research result.", "stop_reason": "end_turn", "usage": {}},
            # Re-planner: no new tasks needed
            {"model": "mock", "content": "No new tasks needed.", "stop_reason": "end_turn", "usage": {}},
            # Synthesizer turn 1: final response
            {"model": "mock", "content": "Final synthesized answer.", "stop_reason": "end_turn", "usage": {}},
        ]
    )

    # Set up runner
    from agentic_primitives_gateway.agents.runner import AgentRunner

    agent_runner = AgentRunner()
    agent_runner.set_store(agent_store)

    runner = TeamRunner()
    runner.set_stores(agent_store, team_store, agent_runner)

    # Patch the registry in all modules that use it
    with (
        patch("agentic_primitives_gateway.agents.team_runner.registry") as mock_reg,
        patch("agentic_primitives_gateway.agents.team_agent_loop.registry") as mock_loop_reg,
        patch("agentic_primitives_gateway.agents.team_prompts.registry") as mock_prompts_reg,
        patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_handlers_reg,
    ):
        for m in (mock_reg, mock_loop_reg, mock_prompts_reg):
            m.llm = mock_gw
            m.tasks = tasks_provider
        mock_handlers_reg.tasks = tasks_provider

        result = await runner.run(team_spec, "Tell me about AI")

    assert result.team_name == "test-team"
    assert result.phase == "done"
    assert result.response == "Final synthesized answer."
    assert result.tasks_created >= 1


@pytest.mark.asyncio
async def test_team_runner_stream(tmp_path: Any) -> None:
    """Test that streaming yields expected event types."""
    agent_store = FileAgentStore(path=str(tmp_path / "agents.json"))
    team_store = FileTeamStore(path=str(tmp_path / "teams.json"))
    tasks_provider = InMemoryTasksProvider()

    planner = AgentSpec(name="planner", model="mock", system_prompt="Plan")
    worker = AgentSpec(name="worker", model="mock", system_prompt="Work")
    synth = AgentSpec(name="synth", model="mock", system_prompt="Synth")
    await agent_store.create(planner)
    await agent_store.create(worker)
    await agent_store.create(synth)

    team_spec = TeamSpec(name="stream-team", planner="planner", synthesizer="synth", workers=["worker"])

    mock_gw = MockLLMProvider()
    mock_gw.set_responses(
        [
            # Planner creates a task
            {
                "model": "mock",
                "content": "Planning",
                "stop_reason": "tool_use",
                "tool_calls": [{"id": "t1", "name": "create_task", "input": {"title": "Do work"}}],
                "usage": {},
            },
            {"model": "mock", "content": "Done planning", "stop_reason": "end_turn", "usage": {}},
            # Worker
            {"model": "mock", "content": "Work done", "stop_reason": "end_turn", "usage": {}},
            # Re-planner: no new tasks
            {"model": "mock", "content": "No new tasks.", "stop_reason": "end_turn", "usage": {}},
            # Synthesizer
            {"model": "mock", "content": "All done", "stop_reason": "end_turn", "usage": {}},
        ]
    )

    from agentic_primitives_gateway.agents.runner import AgentRunner

    agent_runner = AgentRunner()
    agent_runner.set_store(agent_store)

    runner = TeamRunner()
    runner.set_stores(agent_store, team_store, agent_runner)

    with (
        patch("agentic_primitives_gateway.agents.team_runner.registry") as mock_reg,
        patch("agentic_primitives_gateway.agents.team_agent_loop.registry") as mock_loop_reg,
        patch("agentic_primitives_gateway.agents.team_prompts.registry") as mock_prompts_reg,
        patch("agentic_primitives_gateway.agents.tools.handlers.registry") as mock_handlers_reg,
    ):
        for m in (mock_reg, mock_loop_reg, mock_prompts_reg):
            m.llm = mock_gw
            m.tasks = tasks_provider
        mock_handlers_reg.tasks = tasks_provider

        events = []
        async for event in runner.run_stream(team_spec, "Stream test"):
            events.append(event)

    event_types = [e["type"] for e in events]
    assert "team_start" in event_types
    assert "phase_change" in event_types
    assert "tasks_created" in event_types
    assert "done" in event_types

    # Verify phase transitions
    phases = [e["phase"] for e in events if e["type"] == "phase_change"]
    assert phases == ["planning", "execution", "synthesis"]
