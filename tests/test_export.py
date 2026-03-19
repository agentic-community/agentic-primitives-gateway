"""Tests for agent and team export functionality."""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.agents.export import export_agent, export_team
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig
from agentic_primitives_gateway.models.teams import TeamSpec


def _compile(code: str, name: str = "test.py") -> None:
    """Assert that generated code is valid Python."""
    compile(code, name, "exec")


def _make_agent(name: str = "test-agent", **kwargs) -> AgentSpec:
    kwargs.setdefault("model", "us.anthropic.claude-sonnet-4-20250514-v1:0")
    kwargs.setdefault("system_prompt", "You are a test agent.")
    return AgentSpec(name=name, **kwargs)


class TestExportAgentBasic:
    """Test basic agent export."""

    def test_compiles(self):
        code = export_agent(_make_agent())
        _compile(code)

    def test_contains_model_id(self):
        code = export_agent(_make_agent())
        assert "us.anthropic.claude-sonnet-4-20250514-v1:0" in code

    def test_contains_system_prompt(self):
        spec = _make_agent(system_prompt="You are a helpful bot.")
        code = export_agent(spec)
        assert "You are a helpful bot." in code

    def test_contains_gateway_client(self):
        code = export_agent(_make_agent())
        assert "AgenticPlatformClient" in code

    def test_contains_auth_setup(self):
        code = export_agent(_make_agent())
        assert "fetch_token_from_env" in code
        assert "set_auth_token" in code

    def test_contains_main_loop(self):
        code = export_agent(_make_agent())
        assert "async def run(" in code
        assert "async def main(" in code
        assert "asyncio.run(main())" in code

    def test_usage_includes_jwt_token(self):
        code = export_agent(_make_agent())
        assert "JWT_TOKEN" in code

    def test_max_turns_and_temperature(self):
        spec = _make_agent(max_turns=50, temperature=0.5)
        code = export_agent(spec)
        assert "MAX_TURNS = 50" in code
        assert "TEMPERATURE = 0.5" in code


class TestExportAgentMemory:
    """Test agent export with memory primitive."""

    def test_compiles(self):
        spec = _make_agent(primitives={"memory": PrimitiveConfig(enabled=True)})
        _compile(export_agent(spec))

    def test_memory_tools_included(self):
        spec = _make_agent(primitives={"memory": PrimitiveConfig(enabled=True)})
        code = export_agent(spec)
        assert '"remember"' in code
        assert '"recall"' in code
        assert '"search_memory"' in code
        assert "store_memory" in code

    def test_namespace_set(self):
        spec = _make_agent(primitives={"memory": PrimitiveConfig(enabled=True)})
        code = export_agent(spec)
        assert 'NAMESPACE = "agent:test-agent"' in code


class TestExportAgentBrowser:
    """Test agent export with browser primitive."""

    def test_compiles(self):
        spec = _make_agent(primitives={"browser": PrimitiveConfig(enabled=True)})
        _compile(export_agent(spec))

    def test_browser_tools_included(self):
        spec = _make_agent(primitives={"browser": PrimitiveConfig(enabled=True)})
        code = export_agent(spec)
        assert '"navigate"' in code
        assert '"read_page"' in code
        assert "browser_navigate" in code

    def test_session_management_included(self):
        spec = _make_agent(primitives={"browser": PrimitiveConfig(enabled=True)})
        code = export_agent(spec)
        assert "_ensure_session" in code
        assert "_cleanup_sessions" in code
        assert "start_browser_session" in code

    def test_session_id_passed_to_calls(self):
        spec = _make_agent(primitives={"browser": PrimitiveConfig(enabled=True)})
        code = export_agent(spec)
        assert '_ensure_session("browser"' in code
        assert "browser_navigate(sid," in code


class TestExportAgentCodeInterpreter:
    """Test agent export with code interpreter primitive."""

    def test_compiles(self):
        spec = _make_agent(primitives={"code_interpreter": PrimitiveConfig(enabled=True)})
        _compile(export_agent(spec))

    def test_session_management_included(self):
        spec = _make_agent(primitives={"code_interpreter": PrimitiveConfig(enabled=True)})
        code = export_agent(spec)
        assert "_ensure_session" in code
        assert "start_code_session" in code
        assert '_ensure_session("code_interpreter"' in code


class TestExportAgentNoSessions:
    """Test that session management is omitted when not needed."""

    def test_memory_only_no_sessions(self):
        spec = _make_agent(primitives={"memory": PrimitiveConfig(enabled=True)})
        code = export_agent(spec)
        assert "_ensure_session" not in code
        assert "_cleanup_sessions" not in code

    def test_tools_only_no_sessions(self):
        spec = _make_agent(primitives={"tools": PrimitiveConfig(enabled=True)})
        code = export_agent(spec)
        assert "_ensure_session" not in code


class TestExportAgentDelegation:
    """Test agent export with agent-as-tool delegation."""

    def test_compiles(self):
        coord = _make_agent(
            name="coordinator",
            primitives={"agents": PrimitiveConfig(enabled=True, tools=["researcher"])},
        )
        researcher = _make_agent(name="researcher")
        _compile(export_agent(coord, all_specs={"researcher": researcher}))

    def test_call_tools_included(self):
        coord = _make_agent(
            name="coordinator",
            primitives={"agents": PrimitiveConfig(enabled=True, tools=["researcher", "coder"])},
        )
        researcher = _make_agent(name="researcher")
        coder = _make_agent(name="coder")
        code = export_agent(coord, all_specs={"researcher": researcher, "coder": coder})
        assert '"call_researcher"' in code
        assert '"call_coder"' in code

    def test_sub_agent_configs_included(self):
        coord = _make_agent(
            name="coordinator",
            primitives={"agents": PrimitiveConfig(enabled=True, tools=["researcher"])},
        )
        researcher = _make_agent(name="researcher", system_prompt="You are a researcher.")
        code = export_agent(coord, all_specs={"researcher": researcher})
        assert "SUB_AGENTS" in code
        assert "run_agent" in code

    def test_no_sub_agents_without_specs(self):
        coord = _make_agent(
            name="coordinator",
            primitives={"agents": PrimitiveConfig(enabled=True, tools=["researcher"])},
        )
        code = export_agent(coord)
        assert "SUB_AGENTS" not in code


class TestExportAgentSharedPools:
    """Test agent export with shared memory pools (Level 2)."""

    def test_compiles(self):
        spec = _make_agent(
            primitives={"memory": PrimitiveConfig(enabled=True, shared_namespaces=["project:alpha"])},
        )
        _compile(export_agent(spec))

    def test_shared_pools_included(self):
        spec = _make_agent(
            primitives={"memory": PrimitiveConfig(enabled=True, shared_namespaces=["project:alpha", "team:research"])},
        )
        code = export_agent(spec)
        assert "SHARED_POOLS" in code
        assert "project:alpha" in code
        assert "team:research" in code
        assert '"share_to"' in code
        assert '"search_pool"' in code

    def test_no_pools_without_config(self):
        spec = _make_agent(primitives={"memory": PrimitiveConfig(enabled=True)})
        code = export_agent(spec)
        assert "SHARED_POOLS" not in code


class TestExportAgentCombined:
    """Test agent export with multiple primitives combined."""

    def test_all_primitives(self):
        spec = _make_agent(
            primitives={
                "memory": PrimitiveConfig(enabled=True, shared_namespaces=["shared:pool"]),
                "browser": PrimitiveConfig(enabled=True),
                "code_interpreter": PrimitiveConfig(enabled=True),
                "tools": PrimitiveConfig(enabled=True),
                "agents": PrimitiveConfig(enabled=True, tools=["helper"]),
            },
        )
        helper = _make_agent(name="helper")
        code = export_agent(spec, all_specs={"helper": helper})
        _compile(code)
        assert "store_memory" in code
        assert "browser_navigate" in code
        assert "execute_code" in code
        assert "search_tools" in code
        assert "call_helper" in code
        assert "SHARED_POOLS" in code
        assert "_ensure_session" in code

    def test_triple_quote_escape(self):
        spec = _make_agent(system_prompt='Use """ for docstrings.')
        code = export_agent(spec)
        _compile(code)


class TestExportTeamBasic:
    """Test basic team export."""

    @pytest.fixture
    def team_specs(self):
        return {
            "planner": _make_agent(name="planner", system_prompt="Plan tasks."),
            "synthesizer": _make_agent(name="synthesizer", system_prompt="Synthesize."),
            "researcher": _make_agent(name="researcher", primitives={"memory": PrimitiveConfig(enabled=True)}),
        }

    def test_compiles(self, team_specs):
        team = TeamSpec(name="test-team", planner="planner", synthesizer="synthesizer", workers=["researcher"])
        _compile(export_team(team, team_specs))

    def test_contains_planner(self, team_specs):
        team = TeamSpec(name="test-team", planner="planner", synthesizer="synthesizer", workers=["researcher"])
        code = export_team(team, team_specs)
        assert "async def plan(" in code
        assert "PLANNER_MODEL" in code

    def test_contains_synthesizer(self, team_specs):
        team = TeamSpec(name="test-team", planner="planner", synthesizer="synthesizer", workers=["researcher"])
        code = export_team(team, team_specs)
        assert "async def synthesize(" in code
        assert "SYNTH_MODEL" in code

    def test_contains_workers(self, team_specs):
        team = TeamSpec(name="test-team", planner="planner", synthesizer="synthesizer", workers=["researcher"])
        code = export_team(team, team_specs)
        assert "WORKERS" in code
        assert "async def execute_task(" in code

    def test_contains_auth(self, team_specs):
        team = TeamSpec(name="test-team", planner="planner", synthesizer="synthesizer", workers=["researcher"])
        code = export_team(team, team_specs)
        assert "fetch_token_from_env" in code

    def test_contains_main(self, team_specs):
        team = TeamSpec(name="test-team", planner="planner", synthesizer="synthesizer", workers=["researcher"])
        code = export_team(team, team_specs)
        assert "async def main(" in code
        assert "asyncio.run(main())" in code


class TestExportTeamSharedMemory:
    """Test team export with shared memory."""

    @pytest.fixture
    def team_specs(self):
        return {
            "planner": _make_agent(name="planner"),
            "synthesizer": _make_agent(name="synthesizer"),
            "researcher": _make_agent(name="researcher", primitives={"memory": PrimitiveConfig(enabled=True)}),
        }

    def test_compiles(self, team_specs):
        team = TeamSpec(
            name="test-team",
            planner="planner",
            synthesizer="synthesizer",
            workers=["researcher"],
            shared_memory_namespace="team:{team_name}",
        )
        _compile(export_team(team, team_specs))

    def test_shared_namespace_included(self, team_specs):
        team = TeamSpec(
            name="test-team",
            planner="planner",
            synthesizer="synthesizer",
            workers=["researcher"],
            shared_memory_namespace="team:{team_name}",
        )
        code = export_team(team, team_specs)
        assert "SHARED_NAMESPACE" in code
        assert "team:test-team" in code
        assert '"share_finding"' in code
        assert '"search_shared"' in code

    def test_no_shared_without_config(self, team_specs):
        team = TeamSpec(name="test-team", planner="planner", synthesizer="synthesizer", workers=["researcher"])
        code = export_team(team, team_specs)
        assert "SHARED_NAMESPACE" not in code


class TestExportTeamSessions:
    """Test team export with session-requiring workers."""

    def test_browser_worker_has_sessions(self):
        specs = {
            "planner": _make_agent(name="planner"),
            "synthesizer": _make_agent(name="synthesizer"),
            "browser-worker": _make_agent(name="browser-worker", primitives={"browser": PrimitiveConfig(enabled=True)}),
        }
        team = TeamSpec(name="t", planner="planner", synthesizer="synthesizer", workers=["browser-worker"])
        code = export_team(team, specs)
        _compile(code)
        assert "_ensure_session" in code
        assert "_cleanup_sessions" in code

    def test_memory_only_workers_no_sessions(self):
        specs = {
            "planner": _make_agent(name="planner"),
            "synthesizer": _make_agent(name="synthesizer"),
            "writer": _make_agent(name="writer", primitives={"memory": PrimitiveConfig(enabled=True)}),
        }
        team = TeamSpec(name="t", planner="planner", synthesizer="synthesizer", workers=["writer"])
        code = export_team(team, specs)
        _compile(code)
        assert "_ensure_session" not in code


class TestExportTeamMultipleWorkers:
    """Test team export with multiple workers."""

    def test_compiles(self):
        specs = {
            "planner": _make_agent(name="planner"),
            "synthesizer": _make_agent(name="synthesizer"),
            "researcher": _make_agent(
                name="researcher",
                primitives={"memory": PrimitiveConfig(enabled=True), "browser": PrimitiveConfig(enabled=True)},
            ),
            "coder": _make_agent(name="coder", primitives={"code_interpreter": PrimitiveConfig(enabled=True)}),
        }
        team = TeamSpec(
            name="research-team",
            planner="planner",
            synthesizer="synthesizer",
            workers=["researcher", "coder"],
            shared_memory_namespace="team:{team_name}",
        )
        code = export_team(team, specs)
        _compile(code)
        assert "researcher" in code
        assert "coder" in code
        assert "SHARED_NAMESPACE" in code
        assert "_ensure_session" in code


class TestExportRegionExtraction:
    """Test region extraction from model IDs."""

    def test_us_region(self):
        spec = _make_agent(model="us.anthropic.claude-sonnet-4-20250514-v1:0")
        code = export_agent(spec)
        assert 'REGION = "us-east-1"' in code

    def test_eu_region(self):
        spec = _make_agent(model="eu.anthropic.claude-sonnet-4-20250514-v1:0")
        code = export_agent(spec)
        assert 'REGION = "eu-west-1"' in code

    def test_ap_region(self):
        spec = _make_agent(model="ap.anthropic.claude-sonnet-4-20250514-v1:0")
        code = export_agent(spec)
        assert 'REGION = "ap-northeast-1"' in code

    def test_default_region(self):
        spec = _make_agent(model="anthropic.claude-v2")
        code = export_agent(spec)
        assert 'REGION = "us-east-1"' in code
