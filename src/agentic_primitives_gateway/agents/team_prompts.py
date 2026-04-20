"""Prompt builders for team planning, re-planning, synthesis, and worker tasks."""

from __future__ import annotations

import logging

from agentic_primitives_gateway.agents.store import AgentStore
from agentic_primitives_gateway.models.tasks import TaskStatus
from agentic_primitives_gateway.models.teams import TeamSpec
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)


async def build_worker_descriptions(team_spec: TeamSpec, agent_store: AgentStore) -> str:
    """Format worker names, descriptions, and capabilities for prompts.

    Resolves worker refs in the team owner's namespace first, then falls
    back to ``system`` — same rule used by the team runner.
    """
    from agentic_primitives_gateway.agents.team_runner import _resolve_team_agent

    descriptions = []
    for w in set(team_spec.workers):
        spec = await _resolve_team_agent(agent_store, team_spec.owner_id, w)
        desc = f"  - {w}"
        if spec and spec.description:
            desc += f": {spec.description}"
        prims = []
        if spec:
            prims = [p for p, c in spec.primitives.items() if c.enabled]
        if prims:
            desc += f" (capabilities: {', '.join(prims)})"
        descriptions.append(desc)
    return "\n".join(descriptions)


async def build_planner_prompt(team_spec: TeamSpec, message: str, agent_store: AgentStore) -> str:
    """Build the initial planning prompt with worker info and guidelines."""
    worker_list = await build_worker_descriptions(team_spec, agent_store)
    shared_hint = ""
    if team_spec.shared_memory_namespace:
        shared_hint = (
            "- Workers have SHARED MEMORY: they can store findings with share_finding and "
            "read each other's findings with search_shared/read_shared. Design tasks to "
            "leverage this — e.g. a researcher stores results that a coder then reads.\n"
        )

    return (
        f"You are a task planner. Decompose the following request into concrete, "
        f"actionable tasks that can be worked on by team members.\n\n"
        f"Available team members:\n{worker_list}\n\n"
        f"Guidelines:\n"
        f"- ALWAYS set assigned_to to the name of the best worker for each task\n"
        f"- Only create tasks where you can write a SPECIFIC, actionable description right now\n"
        f"- Do NOT create tasks that depend on unknown information (e.g. 'implement the frameworks' when "
        f"you don't know which frameworks yet). Those tasks will be created automatically by re-planning "
        f"after the research results come back.\n"
        f"- Each task should produce a complete, self-contained deliverable\n"
        f"- Use dependencies (depends_on with task IDs) when a task needs results from another\n"
        f"- Use priority to indicate importance (higher = more important)\n"
        f"{shared_hint}\n"
        f"Use the create_task tool to add each task to the board.\n\n"
        f"Request: {message}"
    )


async def build_replan_prompt(
    team_spec: TeamSpec,
    team_run_id: str,
    original_message: str,
    agent_store: AgentStore,
) -> str | None:
    """Build a re-planning prompt from completed task results.

    Returns None if there are no completed tasks to review.
    """
    all_tasks = await registry.tasks.list_tasks(team_run_id)
    completed = [t for t in all_tasks if t.status == TaskStatus.DONE]
    pending_or_active = [
        t for t in all_tasks if t.status in (TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS)
    ]

    if not completed:
        return None

    worker_list = await build_worker_descriptions(team_spec, agent_store)

    completed_summary = []
    for t in completed:
        result_preview = (t.result or "")[:500]
        completed_summary.append(f"  - [{t.id}] {t.title} (assigned_to: {t.assigned_to})\n    Result: {result_preview}")

    pending_summary = []
    for t in pending_or_active:
        pending_summary.append(
            f"  - [{t.id}] {t.title} (status: {t.status}, "
            f"assigned_to: {t.suggested_worker or t.assigned_to or 'unassigned'})"
        )

    return (
        f"You are a task planner reviewing progress on a team request.\n\n"
        f"Original request: {original_message}\n\n"
        f"Available team members:\n{worker_list}\n\n"
        f"Completed tasks:\n"
        + "\n".join(completed_summary)
        + "\n\n"
        + (
            "Pending/active tasks:\n" + "\n".join(pending_summary) + "\n\n"
            if pending_summary
            else "No pending tasks.\n\n"
        )
        + "Based on the completed task results, do any NEW follow-up tasks need to be created?\n\n"
        "Guidelines:\n"
        "- Review the completed results carefully — if they reveal specific items "
        "(e.g., specific framework names, specific topics), create NEW tasks with those specific details\n"
        "- ALWAYS set assigned_to to the appropriate worker\n"
        "- Set depends_on if the new task needs results from a specific completed task\n"
        "- Do NOT recreate tasks that already exist (completed or pending)\n"
        "- If no new tasks are needed, just respond with text explaining why — do NOT call create_task\n"
        "- Think about whether the original request has been fully addressed by existing tasks\n\n"
        "Use the create_task tool ONLY if new tasks are needed."
    )


async def build_synthesis_prompt(team_run_id: str, original_message: str) -> str:
    """Build the synthesis prompt with all completed task results."""
    all_tasks = await registry.tasks.list_tasks(team_run_id)
    task_summary = []
    for t in all_tasks:
        status_icon = {"done": "OK", "failed": "FAILED"}.get(t.status, t.status)
        result_text = f"\n  Result: {t.result}" if t.result else ""
        notes_text = ""
        if t.notes:
            notes_text = "\n  Notes:\n" + "\n".join(f"    [{n.agent}]: {n.content}" for n in t.notes)
        task_summary.append(f"- [{status_icon}] {t.title}{result_text}{notes_text}")

    return (
        f"You are a synthesizer. The team has completed work on the following request:\n\n"
        f"Original request: {original_message}\n\n"
        f"Task results:\n" + "\n".join(task_summary) + "\n\n"
        "Synthesize ALL of these results into a single coherent, comprehensive "
        "response that fully addresses the original request. Include all code, "
        "findings, and details from each task. Do not omit any task results. "
        "If you need more detail on any task, use the get_task tool to retrieve it."
    )


def build_task_message(title: str, description: str, upstream_context: str) -> str:
    """Build the message sent to a worker for a specific task."""
    msg = f"You are working as part of a team. Your task:\n\nTitle: {title}\nDescription: {description}\n"
    if upstream_context:
        msg += f"\nContext from completed upstream tasks:\n{upstream_context}\n"
    msg += (
        "\nComplete this task thoroughly using your available tools. "
        "Your final response will be recorded as the task result and shared with "
        "the team, so make it detailed and self-contained. Include all relevant "
        "information, code, findings, or analysis in your response."
    )
    return msg
