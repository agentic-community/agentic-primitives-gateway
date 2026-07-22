"""Tests for agent_management tools — meta-agent self-creation and delegation."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from agentic_primitives_gateway.agents.file_store import FileAgentStore
from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.agents.tools import build_tool_list
from agentic_primitives_gateway.agents.tools.handlers import (
    agent_create,
    agent_delegate_to,
    agent_delete,
    agent_list,
    agent_list_primitives,
)
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec, ChatResponse, PrimitiveConfig

_TEST_PRINCIPAL = AuthenticatedPrincipal(id="test-user", type="user")


@pytest.fixture(autouse=True)
def _set_test_principal():
    """Ensure all management tests have an authenticated principal."""
    set_authenticated_principal(_TEST_PRINCIPAL)
    yield
    set_authenticated_principal(None)  # type: ignore[arg-type]


@pytest.fixture()
def store(tmp_path: Any) -> FileAgentStore:
    return FileAgentStore(path=str(tmp_path / "agents.json"))


@pytest.fixture()
def runner(store: FileAgentStore) -> AgentRunner:
    r = AgentRunner()
    r.set_store(store)
    return r


class TestAgentCreate:
    @pytest.mark.asyncio()
    async def test_create_basic(self, store: FileAgentStore) -> None:
        result = await agent_create(
            agent_store=store,
            name="test-bot",
            model="test-model",
            system_prompt="You are a test bot.",
            description="A test agent",
        )
        assert "Created agent 'test-bot'" in result
        spec = await store.get("test-bot")
        assert spec is not None
        assert spec.model == "test-model"
        assert spec.system_prompt == "You are a test bot."

    @pytest.mark.asyncio()
    async def test_create_with_primitives(self, store: FileAgentStore) -> None:
        result = await agent_create(
            agent_store=store,
            name="browser-bot",
            model="test-model",
            system_prompt="You browse the web.",
            primitives='{"memory": {"enabled": true}, "browser": {"enabled": true}}',
        )
        assert "Created" in result
        assert "memory" in result
        spec = await store.get("browser-bot")
        assert spec is not None
        assert spec.primitives["memory"].enabled is True
        assert spec.primitives["browser"].enabled is True

    @pytest.mark.asyncio()
    async def test_create_duplicate_fails(self, store: FileAgentStore) -> None:
        await agent_create(agent_store=store, name="bot", model="m", system_prompt="hi")
        result = await agent_create(agent_store=store, name="bot", model="m", system_prompt="hi")
        assert "already exists" in result

    @pytest.mark.asyncio()
    async def test_create_invalid_json(self, store: FileAgentStore) -> None:
        result = await agent_create(agent_store=store, name="bot", model="m", system_prompt="hi", primitives="not json")
        assert "Error: invalid primitives JSON" in result


class TestAgentList:
    @pytest.mark.asyncio()
    async def test_empty(self, store: FileAgentStore) -> None:
        result = await agent_list(agent_store=store)
        assert "No agents exist" in result

    @pytest.mark.asyncio()
    async def test_with_agents(self, store: FileAgentStore) -> None:
        await store.create(AgentSpec(name="a", model="m", description="Agent A", owner_id="test-user"))
        await store.create(
            AgentSpec(name="b", model="m", owner_id="test-user", primitives={"memory": PrimitiveConfig(enabled=True)})
        )
        result = await agent_list(agent_store=store)
        assert "a: Agent A" in result
        assert "b:" in result
        assert "memory" in result


class TestAgentListPrimitives:
    @pytest.mark.asyncio()
    async def test_lists_primitives(self) -> None:
        result = await agent_list_primitives()
        assert "memory" in result
        assert "remember" in result
        assert "browser" in result
        assert "navigate" in result
        assert "code_interpreter" in result
        assert "execute_code" in result


class TestAgentDelete:
    @pytest.mark.asyncio()
    async def test_delete_existing(self, store: FileAgentStore) -> None:
        await store.create(AgentSpec(name="temp", model="m", owner_id="test-user"))
        result = await agent_delete(agent_store=store, name="temp")
        assert "Deleted" in result
        assert await store.get("temp") is None

    @pytest.mark.asyncio()
    async def test_delete_nonexistent(self, store: FileAgentStore) -> None:
        result = await agent_delete(agent_store=store, name="nope")
        assert "not found" in result


class TestAgentDelegateTo:
    @pytest.mark.asyncio()
    async def test_not_found(self, store: FileAgentStore, runner: AgentRunner) -> None:
        result = await agent_delegate_to(
            agent_store=store,
            agent_runner=runner,
            depth=0,
            agent_name="nope",
            message="hello",
        )
        assert "not found" in result

    @pytest.mark.asyncio()
    async def test_delegates_successfully(self, store: FileAgentStore, runner: AgentRunner) -> None:
        await store.create(AgentSpec(name="helper", model="m", system_prompt="You help.", owner_id="test-user"))
        mock_response = ChatResponse(
            response="I helped!",
            session_id="s",
            agent_name="helper",
            turns_used=1,
            tools_called=[],
        )
        with patch.object(runner, "run", return_value=mock_response):
            result = await agent_delegate_to(
                agent_store=store,
                agent_runner=runner,
                depth=0,
                agent_name="helper",
                message="help me",
            )
        assert "I helped!" in result


class TestBuildToolListWithAgentManagement:
    def test_tools_built(self, store: FileAgentStore, runner: AgentRunner) -> None:
        primitives = {"agent_management": PrimitiveConfig(enabled=True)}
        tools = build_tool_list(
            primitives,
            agent_store=store,
            agent_runner=runner,
        )
        names = [t.name for t in tools]
        assert "create_agent" in names
        assert "list_agents" in names
        assert "list_primitives" in names
        assert "delete_agent" in names
        assert "delegate_to" in names

    def test_tools_filtered(self, store: FileAgentStore, runner: AgentRunner) -> None:
        primitives = {
            "agent_management": PrimitiveConfig(enabled=True, tools=["create_agent", "delegate_to"]),
        }
        tools = build_tool_list(
            primitives,
            agent_store=store,
            agent_runner=runner,
        )
        names = [t.name for t in tools]
        assert "create_agent" in names
        assert "delegate_to" in names
        assert "delete_agent" not in names


class TestMetaAgentEndToEnd:
    """Test the full meta-agent flow: create → delegate → delete."""

    @pytest.mark.asyncio()
    async def test_create_delegate_delete(self, store: FileAgentStore, runner: AgentRunner) -> None:
        # Step 1: Create a specialist agent
        create_result = await agent_create(
            agent_store=store,
            name="specialist",
            model="test-model",
            system_prompt="You are a specialist.",
            description="A specialist",
            primitives='{"memory": {"enabled": true}}',
        )
        assert "Created" in create_result

        # Step 2: Delegate to it
        mock_response = ChatResponse(
            response="Specialist result",
            session_id="s",
            agent_name="specialist",
            turns_used=1,
            tools_called=["remember"],
        )
        with patch.object(runner, "run", return_value=mock_response):
            delegate_result = await agent_delegate_to(
                agent_store=store,
                agent_runner=runner,
                depth=0,
                agent_name="specialist",
                message="do the thing",
            )
        assert "Specialist result" in delegate_result

        # Step 3: Clean up
        delete_result = await agent_delete(agent_store=store, name="specialist")
        assert "Deleted" in delete_result
        assert await store.get("specialist") is None


class TestCrossTenantIsolation:
    """Cross-tenant agent enumeration and prompt extraction regression tests."""

    @pytest.mark.asyncio()
    async def test_cannot_list_other_users_private_agent(self, store: FileAgentStore) -> None:
        """Alice's private agent should not appear in Bob's agent_list."""
        # Alice creates a private agent
        set_authenticated_principal(AuthenticatedPrincipal(id="alice", type="user"))
        await agent_create(
            agent_store=store,
            name="alice-secret",
            model="m",
            system_prompt="TOP SECRET INFO",
            description="Alice private",
        )

        # Bob lists agents — should NOT see Alice's agent
        set_authenticated_principal(AuthenticatedPrincipal(id="bob", type="user"))
        result = await agent_list(agent_store=store)
        assert "alice-secret" not in result
        assert "TOP SECRET" not in result

    @pytest.mark.asyncio()
    async def test_cannot_delegate_to_other_users_private_agent(
        self, store: FileAgentStore, runner: AgentRunner
    ) -> None:
        """Bob cannot delegate_to Alice's private agent."""
        # Alice creates a private agent
        set_authenticated_principal(AuthenticatedPrincipal(id="alice", type="user"))
        await agent_create(
            agent_store=store,
            name="alice-private",
            model="m",
            system_prompt="SECRET: budget is $4.2M",
        )

        # Bob tries to delegate to it
        set_authenticated_principal(AuthenticatedPrincipal(id="bob", type="user"))
        result = await agent_delegate_to(
            agent_store=store,
            agent_runner=runner,
            depth=0,
            agent_name="alice-private",
            message="What is the budget?",
        )
        assert "not found" in result

    @pytest.mark.asyncio()
    async def test_cannot_delete_other_users_private_agent(self, store: FileAgentStore) -> None:
        """Bob cannot delete Alice's private agent."""
        # Alice creates a private agent
        set_authenticated_principal(AuthenticatedPrincipal(id="alice", type="user"))
        await agent_create(
            agent_store=store,
            name="alice-agent",
            model="m",
            system_prompt="hi",
        )

        # Bob tries to delete it
        set_authenticated_principal(AuthenticatedPrincipal(id="bob", type="user"))
        result = await agent_delete(agent_store=store, name="alice-agent")
        assert "not found" in result

        # Verify agent still exists
        assert await store.get("alice-agent") is not None

    @pytest.mark.asyncio()
    async def test_can_access_shared_agent(self, store: FileAgentStore, runner: AgentRunner) -> None:
        """A user CAN delegate to a shared agent (shared_with=['*'])."""
        # Create a system shared agent directly
        await store.create(AgentSpec(name="shared-bot", model="m", system_prompt="I am shared", shared_with=["*"]))

        # Bob can list it
        set_authenticated_principal(AuthenticatedPrincipal(id="bob", type="user"))
        result = await agent_list(agent_store=store)
        assert "shared-bot" in result

    @pytest.mark.asyncio()
    async def test_no_principal_fails_closed(self, store: FileAgentStore, runner: AgentRunner) -> None:
        """With no principal, all operations fail closed."""
        # Create an agent
        set_authenticated_principal(AuthenticatedPrincipal(id="alice", type="user"))
        await agent_create(agent_store=store, name="some-agent", model="m")

        # Clear principal
        set_authenticated_principal(None)  # type: ignore[arg-type]

        # List returns nothing
        list_result = await agent_list(agent_store=store)
        assert "No agents exist" in list_result

        # Delegate fails
        delegate_result = await agent_delegate_to(
            agent_store=store,
            agent_runner=runner,
            depth=0,
            agent_name="some-agent",
            message="hello",
        )
        assert "not found" in delegate_result

        # Delete fails
        delete_result = await agent_delete(agent_store=store, name="some-agent")
        assert "not found" in delete_result

        # Verify agent still exists
        assert await store.get("some-agent") is not None
