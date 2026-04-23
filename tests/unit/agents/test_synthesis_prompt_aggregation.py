"""Intent-level test: synthesis prompt includes every completed task's result.

Contract (CLAUDE.md Teams/synthesis): "a synthesizer produces a final
response" combining all worker results.  The contract is that the
synthesizer is told about every task's outcome — if even one is
missing from the prompt, the final response silently omits that
work, and users see incomplete synthesis without any error signal.

No existing test verifies the prompt content.  ``team_runner.py``
tests go end-to-end and check the final string, which conflates
"synthesis prompt contained task X" with "LLM happened to echo X".
A regression where ``build_synthesis_prompt`` silently dropped
tasks (e.g., early return on the first failed task, or accidentally
filtered by assigned_to) would slip past existing coverage.

Also covers task notes: notes from multiple workers on the same
task must all appear in the prompt (not just the last one added).
"""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.agents.team_prompts import build_synthesis_prompt
from agentic_primitives_gateway.models.tasks import TaskNote, TaskStatus
from agentic_primitives_gateway.primitives.tasks.in_memory import InMemoryTasksProvider
from agentic_primitives_gateway.registry import registry

RUN_ID = "run-synth"


@pytest.fixture
def provider():
    """Install an InMemoryTasksProvider on the registry for the
    duration of the test.  The synthesis prompt reads via
    ``registry.tasks.list_tasks``.  Mirrors the fixture pattern in
    ``test_team_runner.py::_use_in_memory_tasks``.
    """
    from agentic_primitives_gateway.registry import _PrimitiveProviders

    p = InMemoryTasksProvider()
    original = registry._primitives.get("tasks")
    registry._primitives["tasks"] = _PrimitiveProviders(
        primitive="tasks", default_name="default", providers={"default": p}
    )
    yield p
    if original is not None:
        registry._primitives["tasks"] = original


class TestSynthesisPromptContainsAllResults:
    @pytest.mark.asyncio
    async def test_three_completed_tasks_all_results_in_prompt(self, provider: InMemoryTasksProvider):
        t1 = await provider.create_task(RUN_ID, "Research frameworks")
        t2 = await provider.create_task(RUN_ID, "Benchmark performance")
        t3 = await provider.create_task(RUN_ID, "Write summary")

        # Complete each task with a distinct result.
        for task, result in [
            (t1, "FastAPI, Django, Flask are the top three"),
            (t2, "FastAPI fastest at 45k req/s; Django 12k; Flask 15k"),
            (t3, "Summary: FastAPI recommended for async workloads"),
        ]:
            await provider.claim_task(RUN_ID, task.id, "worker")
            await provider.update_task(RUN_ID, task.id, status=TaskStatus.DONE, result=result)

        prompt = await build_synthesis_prompt(RUN_ID, "Research Python web frameworks")

        # Every task's title + result must appear in the prompt.
        for title, result in [
            ("Research frameworks", "FastAPI, Django, Flask"),
            ("Benchmark performance", "45k req/s"),
            ("Write summary", "FastAPI recommended"),
        ]:
            assert title in prompt, f"Task title '{title}' missing from synthesis prompt"
            assert result in prompt, (
                f"Task result fragment '{result}' missing from synthesis prompt.  "
                f"Synthesizer would produce an incomplete answer."
            )

        # Original request also makes it into the prompt.
        assert "Research Python web frameworks" in prompt

    @pytest.mark.asyncio
    async def test_failed_task_included_with_status(self, provider: InMemoryTasksProvider):
        """A failed task still appears — the synthesizer should
        acknowledge partial results or report the failure, not
        silently proceed as if that task didn't exist.
        """
        t1 = await provider.create_task(RUN_ID, "Gather data")
        t2 = await provider.create_task(RUN_ID, "Analyze data")

        await provider.claim_task(RUN_ID, t1.id, "w")
        await provider.update_task(RUN_ID, t1.id, status=TaskStatus.DONE, result="Got 1000 records")
        await provider.claim_task(RUN_ID, t2.id, "w")
        await provider.update_task(RUN_ID, t2.id, status=TaskStatus.FAILED, result="OOM during aggregation")

        prompt = await build_synthesis_prompt(RUN_ID, "Do the thing")
        assert "Gather data" in prompt
        assert "1000 records" in prompt
        assert "Analyze data" in prompt
        assert "OOM during aggregation" in prompt, (
            "Failed task result missing — synthesizer has no way to report the failure to the user."
        )

    @pytest.mark.asyncio
    async def test_task_notes_from_multiple_workers_all_appear(self, provider: InMemoryTasksProvider):
        """Notes are how workers communicate during a task.  All
        notes — not just the last one — must be visible to the
        synthesizer so it can reconcile conflicting findings or
        include cross-worker context.
        """
        t = await provider.create_task(RUN_ID, "Investigation")
        await provider.claim_task(RUN_ID, t.id, "researcher")
        await provider.add_note(RUN_ID, t.id, TaskNote(agent="researcher", content="Initial data shows anomaly at 9am"))
        await provider.add_note(RUN_ID, t.id, TaskNote(agent="analyst", content="Anomaly correlates with deployment"))
        await provider.add_note(RUN_ID, t.id, TaskNote(agent="oncaller", content="Rolled back deployment at 9:15am"))
        await provider.update_task(RUN_ID, t.id, status=TaskStatus.DONE, result="Root cause: bad deploy")

        prompt = await build_synthesis_prompt(RUN_ID, "Investigate alert")
        assert "Initial data shows anomaly at 9am" in prompt
        assert "Anomaly correlates with deployment" in prompt
        assert "Rolled back deployment at 9:15am" in prompt, (
            "Later notes missing from synthesis prompt — notes aggregation dropped entries"
        )
        # Author names attached to each note too.
        assert "researcher" in prompt
        assert "analyst" in prompt
        assert "oncaller" in prompt

    @pytest.mark.asyncio
    async def test_empty_task_board_produces_prompt_without_crashing(self, provider: InMemoryTasksProvider):
        """Edge case: no tasks → prompt still renders (not empty,
        not a crash).  Guards against a regression that index-0'd
        into an empty task list.
        """
        prompt = await build_synthesis_prompt(RUN_ID, "Do work")
        # The prompt contains the original request even with no tasks.
        assert "Do work" in prompt
        # Task results section is empty but the scaffolding stays.
        assert "Task results:" in prompt

    @pytest.mark.asyncio
    async def test_task_with_no_result_still_listed(self, provider: InMemoryTasksProvider):
        """A task claimed but not yet marked done (result=None)
        still shows up in the prompt — the synthesizer needs to
        know the task was worked on even if no result text landed.
        """
        t = await provider.create_task(RUN_ID, "Pending task")
        await provider.claim_task(RUN_ID, t.id, "w")

        prompt = await build_synthesis_prompt(RUN_ID, "req")
        assert "Pending task" in prompt
