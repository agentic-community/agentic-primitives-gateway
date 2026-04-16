from __future__ import annotations

import pytest

from agentic_primitives_gateway.primitives.evaluations.noop import NoopEvaluationsProvider
from agentic_primitives_gateway.primitives.memory.noop import NoopMemoryProvider
from agentic_primitives_gateway.primitives.policy.noop import NoopPolicyProvider
from agentic_primitives_gateway.primitives.tasks.noop import NoopTasksProvider

# ── NoopMemoryProvider ───────────────────────────────────────────────


class TestNoopMemory:
    async def test_store_returns_record(self) -> None:
        p = NoopMemoryProvider()
        record = await p.store("ns", "k", "content")
        assert record.namespace == "ns"
        assert record.key == "k"
        assert record.content == "content"

    async def test_retrieve_returns_none(self) -> None:
        p = NoopMemoryProvider()
        assert await p.retrieve("ns", "k") is None

    async def test_search_returns_empty(self) -> None:
        p = NoopMemoryProvider()
        assert await p.search("ns", "q") == []

    async def test_delete_returns_false(self) -> None:
        p = NoopMemoryProvider()
        assert await p.delete("ns", "k") is False

    async def test_list_memories_returns_empty(self) -> None:
        p = NoopMemoryProvider()
        assert await p.list_memories("ns") == []


# ── NoopTasksProvider ────────────────────────────────────────────────


class TestNoopTasks:
    async def test_create_task_raises(self) -> None:
        p = NoopTasksProvider()
        with pytest.raises(NotImplementedError):
            await p.create_task("run", "title")

    async def test_get_task_returns_none(self) -> None:
        p = NoopTasksProvider()
        assert await p.get_task("run", "t1") is None

    async def test_list_tasks_returns_empty(self) -> None:
        p = NoopTasksProvider()
        assert await p.list_tasks("run") == []

    async def test_claim_task_returns_none(self) -> None:
        p = NoopTasksProvider()
        assert await p.claim_task("run", "t1", "agent") is None

    async def test_update_task_returns_none(self) -> None:
        p = NoopTasksProvider()
        assert await p.update_task("run", "t1") is None

    async def test_add_note_returns_none(self) -> None:
        from agentic_primitives_gateway.models.tasks import TaskNote

        p = NoopTasksProvider()
        note = TaskNote(agent="a", content="c")
        assert await p.add_note("run", "t1", note) is None


# ── NoopPolicyProvider ───────────────────────────────────────────────


class TestNoopPolicy:
    async def test_engine_crud(self) -> None:
        p = NoopPolicyProvider()
        engine = await p.create_policy_engine("e1", description="test")
        engine_id = engine["policy_engine_id"]

        fetched = await p.get_policy_engine(engine_id)
        assert fetched["name"] == "e1"

        listing = await p.list_policy_engines()
        assert len(listing["policy_engines"]) == 1

        await p.delete_policy_engine(engine_id)
        with pytest.raises(KeyError):
            await p.get_policy_engine(engine_id)

    async def test_policy_crud(self) -> None:
        p = NoopPolicyProvider()
        engine = await p.create_policy_engine("e2")
        eid = engine["policy_engine_id"]

        policy = await p.create_policy(eid, "permit(...);", description="allow")
        pid = policy["policy_id"]

        fetched = await p.get_policy(eid, pid)
        assert fetched["definition"] == "permit(...);"

        updated = await p.update_policy(eid, pid, "forbid(...);", description="deny")
        assert updated["definition"] == "forbid(...);"

        listing = await p.list_policies(eid)
        assert len(listing["policies"]) == 1

        await p.delete_policy(eid, pid)
        with pytest.raises(KeyError):
            await p.get_policy(eid, pid)

    async def test_get_engine_not_found(self) -> None:
        p = NoopPolicyProvider()
        with pytest.raises(KeyError):
            await p.get_policy_engine("nonexistent")

    async def test_get_policy_not_found(self) -> None:
        p = NoopPolicyProvider()
        with pytest.raises(KeyError):
            await p.get_policy("e", "p")

    async def test_update_policy_not_found(self) -> None:
        p = NoopPolicyProvider()
        with pytest.raises(KeyError):
            await p.update_policy("e", "p", "body")

    async def test_delete_engine_cascades_policies(self) -> None:
        p = NoopPolicyProvider()
        engine = await p.create_policy_engine("e3")
        eid = engine["policy_engine_id"]
        await p.create_policy(eid, "body1")
        await p.create_policy(eid, "body2")
        await p.delete_policy_engine(eid)
        # Policies for this engine should also be gone
        listing = await p.list_policies(eid)
        assert listing["policies"] == []


# ── NoopEvaluationsProvider ──────────────────────────────────────────


class TestNoopEvaluations:
    async def test_evaluator_crud(self) -> None:
        p = NoopEvaluationsProvider()
        evaluator = await p.create_evaluator("ev1", "llm", description="test")
        eid = evaluator["evaluator_id"]

        fetched = await p.get_evaluator(eid)
        assert fetched["name"] == "ev1"

        updated = await p.update_evaluator(eid, description="updated")
        assert updated["description"] == "updated"

        updated2 = await p.update_evaluator(eid, config={"k": "v"})
        assert updated2["config"] == {"k": "v"}

        listing = await p.list_evaluators()
        assert len(listing["evaluators"]) == 1

        await p.delete_evaluator(eid)

    async def test_get_evaluator_not_found(self) -> None:
        p = NoopEvaluationsProvider()
        with pytest.raises(KeyError):
            await p.get_evaluator("nonexistent")

    async def test_update_evaluator_not_found(self) -> None:
        p = NoopEvaluationsProvider()
        with pytest.raises(KeyError):
            await p.update_evaluator("nonexistent")

    async def test_evaluate(self) -> None:
        p = NoopEvaluationsProvider()
        result = await p.evaluate("eid", target="text")
        assert result["results"][0]["label"] == "PASS"

    async def test_online_eval_config_not_implemented(self) -> None:
        p = NoopEvaluationsProvider()
        with pytest.raises(NotImplementedError):
            await p.create_online_evaluation_config("cfg", ["e1"])
        with pytest.raises(NotImplementedError):
            await p.get_online_evaluation_config("cfg")
        with pytest.raises(NotImplementedError):
            await p.delete_online_evaluation_config("cfg")
        with pytest.raises(NotImplementedError):
            await p.list_online_evaluation_configs()
