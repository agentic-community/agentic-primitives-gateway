"""Coverage tests for client methods not exercised by ``test_client.py``.

Fills the gaps surfaced by the server-side coverage report:
- credentials routes (``read_credentials`` / ``write_credentials`` /
  ``delete_credential`` / ``credential_status``)
- audit events SSE stream, provider status
- agent/team export, team-run task retry
- browser full page-interaction surface
- identity credential-provider + workload-identity CRUD
- team version lifecycle + fork + lineage + admin proposals
"""

from __future__ import annotations

import pytest

# ── Credentials ─────────────────────────────────────────────────────


class TestClientCredentials:
    @pytest.mark.asyncio
    async def test_write_then_read_masks_values(self, make_client):
        async with make_client() as client:
            result = await client.write_credentials({"apg.langfuse.public_key": "pk-secret"})
            assert result["status"] == "updated"
            read = await client.read_credentials()
            assert read["attributes"]["apg.langfuse.public_key"] == "***"

    @pytest.mark.asyncio
    async def test_delete_credential(self, make_client):
        async with make_client() as client:
            await client.write_credentials({"apg.langfuse.public_key": "pk"})
            result = await client.delete_credential("apg.langfuse.public_key")
            assert result["status"] == "deleted"
            read = await client.read_credentials()
            assert read["attributes"] == {}

    @pytest.mark.asyncio
    async def test_credential_status(self, make_client):
        async with make_client() as client:
            status = await client.credential_status()
            assert "source" in status


# ── Audit live stream + provider status ─────────────────────────────


class TestClientAuditStream:
    @pytest.mark.asyncio
    async def test_stream_audit_events(self, make_client):
        async with make_client() as client:
            resp = await client.stream_audit_events(action="auth.success")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "text/event-stream"


class TestClientProviderStatus:
    @pytest.mark.asyncio
    async def test_get_provider_status(self, make_client):
        async with make_client() as client:
            status = await client.get_provider_status()
            assert "checks" in status


# ── Agent / Team export + task retry ────────────────────────────────


class TestClientExport:
    @pytest.mark.asyncio
    async def test_export_agent(self, make_client):
        async with make_client() as client:
            await client.create_agent({"name": "exp", "model": "m", "system_prompt": "p"})
            script = await client.export_agent("exp")
            assert "Exported agent" in script
            assert "exp" in script

    @pytest.mark.asyncio
    async def test_export_team(self, make_client):
        async with make_client() as client:
            await client.create_team(
                {
                    "name": "crew",
                    "planner": "planner",
                    "synthesizer": "synth",
                    "workers": ["w1"],
                }
            )
            script = await client.export_team("crew")
            assert "Exported team" in script

    @pytest.mark.asyncio
    async def test_retry_team_task(self, make_client):
        async with make_client() as client:
            resp = await client.retry_team_task("crew", "run-1", "task-1")
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "text/event-stream"


# ── Browser ─────────────────────────────────────────────────────────


class TestClientBrowser:
    @pytest.mark.asyncio
    async def test_session_lifecycle(self, make_client):
        async with make_client() as client:
            session = await client.start_browser_session(session_id="b1", viewport={"width": 800, "height": 600})
            assert session["session_id"] == "b1"

            sessions = await client.list_browser_sessions()
            assert len(sessions["sessions"]) == 1

            info = await client.get_browser_session("b1")
            assert info["session_id"] == "b1"

            await client.stop_browser_session("b1")
            after = await client.list_browser_sessions()
            assert after["sessions"] == []

    @pytest.mark.asyncio
    async def test_navigation_and_interaction(self, make_client):
        async with make_client() as client:
            await client.start_browser_session(session_id="b2")
            nav = await client.browser_navigate("b2", "http://example.com")
            assert nav["url"] == "http://example.com"

            click = await client.browser_click("b2", "#btn")
            assert click["clicked"] is True

            typed = await client.browser_type("b2", "#input", "hello")
            assert typed["typed"] is True

            result = await client.browser_evaluate("b2", "1+1")
            assert result["result"] == 42

            content = await client.browser_get_content("b2")
            assert "content" in content

            shot = await client.browser_screenshot("b2")
            assert shot["format"] == "png"

            live = await client.get_live_view_url("b2")
            assert "url" in live


# ── Identity CRUD ───────────────────────────────────────────────────


class TestClientIdentity:
    @pytest.mark.asyncio
    async def test_credential_provider_crud(self, make_client):
        async with make_client() as client:
            created = await client.create_credential_provider(
                name="google-oauth",
                provider_type="google",
                config={"client_id": "x"},
            )
            assert created["name"] == "google-oauth"

            providers = await client.list_credential_providers()
            assert any(p["name"] == "google-oauth" for p in providers["credential_providers"])

            got = await client.get_credential_provider("google-oauth")
            assert got["provider_type"] == "google"

            updated = await client.update_credential_provider("google-oauth", config={"client_id": "y"})
            assert updated["config"]["client_id"] == "y"

            await client.delete_credential_provider("google-oauth")

    @pytest.mark.asyncio
    async def test_workload_identity_crud(self, make_client):
        async with make_client() as client:
            created = await client.create_workload_identity(
                name="my-agent",
                allowed_return_urls=["https://app/callback"],
            )
            assert created["name"] == "my-agent"

            identities = await client.list_workload_identities()
            assert any(i["name"] == "my-agent" for i in identities["workload_identities"])

            got = await client.get_workload_identity("my-agent")
            assert got["name"] == "my-agent"

            updated = await client.update_workload_identity("my-agent", allowed_return_urls=["https://app/other"])
            assert updated["allowed_return_urls"] == ["https://app/other"]

            await client.delete_workload_identity("my-agent")

    @pytest.mark.asyncio
    async def test_get_token_with_full_options(self, make_client):
        async with make_client() as client:
            # Exercise every optional branch in get_token.
            result = await client.get_token(
                credential_provider="google",
                workload_token="w1",
                auth_flow="USER_FEDERATION",
                scopes=["read", "write"],
                callback_url="https://app/cb",
                force_auth=True,
                session_uri="sess://x",
                custom_state="state",
                custom_parameters={"prompt": "consent"},
            )
            assert result["access_token"] == "mock-token"

    @pytest.mark.asyncio
    async def test_get_workload_token_with_user_context(self, make_client):
        async with make_client() as client:
            result = await client.get_workload_token("my-workload", user_token="u1", user_id="user-42")
            assert result["workload_name"] == "my-workload"

    @pytest.mark.asyncio
    async def test_complete_auth(self, make_client):
        async with make_client() as client:
            # Returns 204; client should not raise.
            await client.complete_auth(session_uri="sess://1", user_token="u", user_id="u1")


# ── Team versioning / fork / lineage / admin proposals ──────────────


class TestClientTeamVersioning:
    @pytest.mark.asyncio
    async def test_list_initial_versions(self, make_client):
        async with make_client() as client:
            await client.create_team({"name": "t1", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            versions = await client.list_team_versions("t1")
            assert len(versions["versions"]) == 1
            assert versions["versions"][0]["version_number"] == 1

    @pytest.mark.asyncio
    async def test_version_lifecycle(self, make_client):
        async with make_client() as client:
            await client.create_team({"name": "t2", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            created = await client.create_team_version("t2", {"commit_message": "v2"})
            vid = created["version_id"]

            got = await client.get_team_version("t2", vid)
            assert got["version_id"] == vid

            proposed = await client.propose_team_version("t2", vid)
            assert proposed["status"] == "proposed"

            approved = await client.approve_team_version("t2", vid)
            assert approved["approved_by"] == "admin"

            deployed = await client.deploy_team_version("t2", vid)
            assert deployed["status"] == "deployed"

    @pytest.mark.asyncio
    async def test_reject_team_version(self, make_client):
        async with make_client() as client:
            await client.create_team({"name": "t3", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            created = await client.create_team_version("t3", {"commit_message": "bad"})
            await client.propose_team_version("t3", created["version_id"])
            rejected = await client.reject_team_version("t3", created["version_id"], "missing tests")
            assert rejected["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_fork_team_with_target_name(self, make_client):
        async with make_client() as client:
            await client.create_team({"name": "upstream", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            fork = await client.fork_team("upstream", target_name="mine", commit_message="fork")
            assert fork["team_name"] == "mine"
            assert fork["forked_from"]["name"] == "upstream"

    @pytest.mark.asyncio
    async def test_team_lineage(self, make_client):
        async with make_client() as client:
            await client.create_team({"name": "root", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            lineage = await client.get_team_lineage("root")
            assert lineage["root_identity"]["name"] == "root"

    @pytest.mark.asyncio
    async def test_admin_team_proposals(self, make_client):
        async with make_client() as client:
            await client.create_team({"name": "t4", "planner": "p", "synthesizer": "s", "workers": ["w"]})
            created = await client.create_team_version("t4", {"commit_message": "v2"})
            await client.propose_team_version("t4", created["version_id"])
            pending = await client.list_pending_team_proposals()
            assert any(v["version_id"] == created["version_id"] for v in pending["versions"])

    @pytest.mark.asyncio
    async def test_admin_agent_proposals(self, make_client):
        async with make_client() as client:
            await client.create_agent({"name": "r", "model": "m", "system_prompt": "p"})
            created = await client.create_agent_version("r", {"description": "v2"})
            await client.propose_agent_version("r", created["version_id"])
            pending = await client.list_pending_agent_proposals()
            assert any(v["version_id"] == created["version_id"] for v in pending["versions"])


# ── Memory strategies + actor listing ───────────────────────────────


class TestClientMemoryCoverage:
    @pytest.mark.asyncio
    async def test_add_and_delete_strategy(self, make_client):
        async with make_client() as client:
            resource = await client.create_memory_resource(name="mem", strategies=[])
            memory_id = resource["memory_id"]
            strat = await client.add_strategy(memory_id, {"type": "semantic"})
            assert strat["strategy_id"] == "strat-1"
            strategies = await client.list_strategies(memory_id)
            assert "strategies" in strategies
            await client.delete_strategy(memory_id, "strat-1")

    @pytest.mark.asyncio
    async def test_list_actors(self, make_client):
        async with make_client() as client:
            await client.create_event("actor-x", "sess", [{"text": "hi", "role": "user"}])
            actors = await client.list_actors()
            assert any(a["actor_id"] == "actor-x" for a in actors["actors"])

    @pytest.mark.asyncio
    async def test_list_memory_sessions(self, make_client):
        async with make_client() as client:
            await client.create_event("actor-y", "sess-1", [{"text": "hi", "role": "user"}])
            sessions = await client.list_memory_sessions("actor-y")
            assert any(s["session_id"] == "sess-1" for s in sessions["sessions"])

    @pytest.mark.asyncio
    async def test_fork_conversation(self, make_client):
        async with make_client() as client:
            branch = await client.fork_conversation(
                "actor", "sess", root_event_id="evt-1", branch_name="branch", messages=[]
            )
            assert "name" in branch

    @pytest.mark.asyncio
    async def test_list_branches(self, make_client):
        async with make_client() as client:
            branches = await client.list_branches("actor", "sess")
            assert "branches" in branches

    @pytest.mark.asyncio
    async def test_get_last_turns(self, make_client):
        async with make_client() as client:
            await client.create_event("actor-turn", "sess-t", [{"text": "a", "role": "user"}])
            turns = await client.get_last_turns("actor-turn", "sess-t", k=5)
            assert "turns" in turns


# ── Code interpreter extended ───────────────────────────────────────


class TestClientPolicyCoverage:
    @pytest.mark.asyncio
    async def test_update_and_delete_policy(self, make_client):
        async with make_client() as client:
            engine = await client.create_policy_engine(name="e")
            eid = engine["policy_engine_id"]
            policy = await client.create_policy(eid, policy_body="permit(principal, action, resource);")
            pid = policy["policy_id"]

            updated = await client.update_policy(
                eid, pid, policy_body="forbid(principal, action, resource);", description="nope"
            )
            assert updated["policy_body"] == "forbid(principal, action, resource);"

            await client.delete_policy(eid, pid)

    @pytest.mark.asyncio
    async def test_policy_generation_lifecycle(self, make_client):
        async with make_client() as client:
            engine = await client.create_policy_engine(name="g")
            eid = engine["policy_engine_id"]

            gen = await client.start_policy_generation(eid, config={"goal": "x"})
            gid = gen["generation_id"]

            listed = await client.list_policy_generations(eid)
            assert any(g["generation_id"] == gid for g in listed["generations"])

            got = await client.get_policy_generation(eid, gid)
            assert got["generation_id"] == gid

            assets = await client.list_policy_generation_assets(eid, gid)
            assert "assets" in assets


class TestClientEvaluationScoresCoverage:
    @pytest.mark.asyncio
    async def test_score_crud(self, make_client):
        async with make_client() as client:
            score = await client.create_evaluation_score(name="accuracy", value=0.9, trace_id="t1", comment="good")
            sid = score["score_id"]

            got = await client.get_evaluation_score(sid)
            assert got["name"] == "accuracy"

            listed = await client.list_evaluation_scores(trace_id="t1", name="accuracy")
            assert any(s["score_id"] == sid for s in listed["scores"])

            await client.delete_evaluation_score(sid)

    @pytest.mark.asyncio
    async def test_online_eval_config_crud(self, make_client):
        async with make_client() as client:
            config = await client.create_online_eval_config(
                name="auto", evaluator_ids=["e1"], config={"sample_rate": 0.1}
            )
            cid = config["config_id"]

            got = await client.get_online_eval_config(cid)
            assert got["name"] == "auto"

            listed = await client.list_online_eval_configs()
            assert any(c["config_id"] == cid for c in listed["configs"])

            await client.delete_online_eval_config(cid)


class TestClientCodeInterpreterCoverage:
    @pytest.mark.asyncio
    async def test_get_session_and_history(self, make_client):
        async with make_client() as client:
            await client.start_code_session(session_id="ci1")
            session = await client.get_code_session("ci1")
            assert session["session_id"] == "ci1"

            history = await client.get_execution_history("ci1", limit=10)
            assert "entries" in history

    @pytest.mark.asyncio
    async def test_upload_and_download_file(self, make_client):
        async with make_client() as client:
            await client.start_code_session(session_id="ci2")
            upload = await client.upload_file("ci2", "test.py", b"print(1)")
            assert upload["filename"] == "test.py"

            content = await client.download_file("ci2", "test.py")
            assert content == b"file content"
