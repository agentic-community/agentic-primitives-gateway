"""Tool handler functions for each primitive.

Each handler wraps a registry method call and returns a string suitable
for inclusion in an LLM conversation as a tool result.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)


# ── Memory ───────────────────────────────────────────────────────────


async def memory_store(namespace: str, key: str, content: str, source: str = "") -> str:
    metadata = {"source": source} if source else {}
    await registry.memory.store(namespace=namespace, key=key, content=content, metadata=metadata)
    return f"Stored memory '{key}'."


async def memory_retrieve(namespace: str, key: str) -> str:
    record = await registry.memory.retrieve(namespace=namespace, key=key)
    if record is None:
        return f"No memory found for key '{key}'."
    return record.content


async def memory_search(namespace: str, query: str, top_k: int = 5) -> str:
    results = await registry.memory.search(namespace=namespace, query=query, top_k=top_k)
    if not results:
        return "No memories found."
    return "\n".join(f"- [{r.score:.2f}] {r.record.key}: {r.record.content}" for r in results)


async def memory_delete(namespace: str, key: str) -> str:
    deleted = await registry.memory.delete(namespace=namespace, key=key)
    return f"Deleted: {deleted}"


async def memory_list(namespace: str, limit: int = 20) -> str:
    records = await registry.memory.list_memories(namespace=namespace, limit=limit)
    if not records:
        return "No memories found."
    return "\n".join(f"- {r.key}: {r.content[:100]}" for r in records)


# ── Code interpreter ────────────────────────────────────────────────


async def code_execute(session_id: str, code: str, language: str = "python") -> str:
    result = await registry.code_interpreter.execute(session_id=session_id, code=code, language=language)
    return json.dumps(result, default=str)


# ── Browser ─────────────────────────────────────────────────────────


async def browser_navigate(session_id: str, url: str) -> str:
    result = await registry.browser.navigate(session_id=session_id, url=url)
    return json.dumps(result, default=str)


async def browser_read_page(session_id: str) -> str:
    return await registry.browser.get_page_content(session_id=session_id)


async def browser_click(session_id: str, selector: str) -> str:
    result = await registry.browser.click(session_id=session_id, selector=selector)
    return json.dumps(result, default=str)


async def browser_type(session_id: str, selector: str, text: str) -> str:
    result = await registry.browser.type_text(session_id=session_id, selector=selector, text=text)
    return json.dumps(result, default=str)


async def browser_screenshot(session_id: str) -> str:
    result = await registry.browser.screenshot(session_id=session_id)
    return f"Screenshot captured ({len(result)} bytes). Use read_page to see text content instead."


async def browser_evaluate_js(session_id: str, expression: str) -> str:
    result = await registry.browser.evaluate(session_id=session_id, expression=expression)
    return json.dumps(result, default=str)


# ── Tools (MCP/external) ────────────────────────────────────────────


async def tools_search(query: str, max_results: int = 10) -> str:
    results = await registry.tools.search_tools(query=query, max_results=max_results)
    if not results:
        return "No tools found."
    return "\n".join(f"- {t.get('name', '?')}: {t.get('description', '')}" for t in results)


async def tools_invoke(tool_name: str, params: str = "{}") -> str:
    try:
        parsed_params = json.loads(params) if isinstance(params, str) else params
    except (json.JSONDecodeError, TypeError):
        parsed_params = {}
    result = await registry.tools.invoke_tool(tool_name=tool_name, params=parsed_params)
    return json.dumps(result, default=str)


# ── Identity ────────────────────────────────────────────────────────


async def identity_get_token(credential_provider: str, scopes: str = "") -> str:
    scope_list = [s.strip() for s in scopes.split(",") if s.strip()] if scopes else None
    kwargs: dict[str, Any] = {"credential_provider": credential_provider, "workload_token": ""}
    if scope_list:
        kwargs["scopes"] = scope_list
    result = await registry.identity.get_token(**kwargs)
    return json.dumps(result, default=str)


async def identity_get_api_key(credential_provider: str) -> str:
    result = await registry.identity.get_api_key(credential_provider=credential_provider, workload_token="")
    return json.dumps(result, default=str)


# ── Task board ─────────────────────────────────────────────────────


async def task_create(
    team_run_id: str,
    agent_name: str,
    title: str,
    description: str = "",
    depends_on: str = "",
    priority: int = 0,
    assigned_to: str = "",
) -> str:
    deps = [d.strip() for d in depends_on.split(",") if d.strip()] if depends_on else []
    task = await registry.tasks.create_task(
        team_run_id=team_run_id,
        title=title,
        description=description,
        created_by=agent_name,
        depends_on=deps,
        priority=priority,
        suggested_worker=assigned_to or None,
    )
    return json.dumps(
        {"id": task.id, "title": task.title, "status": task.status, "assigned_to": task.suggested_worker}, default=str
    )


async def task_list(team_run_id: str, status: str = "", assigned_to: str = "") -> str:
    tasks = await registry.tasks.list_tasks(
        team_run_id=team_run_id,
        status=status or None,
        assigned_to=assigned_to or None,
    )
    if not tasks:
        return "No tasks found."
    lines = []
    for t in tasks:
        deps = f" (depends: {', '.join(t.depends_on)})" if t.depends_on else ""
        assigned = f" [{t.assigned_to}]" if t.assigned_to else ""
        lines.append(f"- [{t.id}] {t.status}{assigned} p{t.priority}: {t.title}{deps}")
    return "\n".join(lines)


async def task_get(team_run_id: str, task_id: str) -> str:
    task = await registry.tasks.get_task(team_run_id=team_run_id, task_id=task_id)
    if task is None:
        return f"Task '{task_id}' not found."
    return json.dumps(task.model_dump(), default=str)


async def task_claim(team_run_id: str, task_id: str, agent_name: str) -> str:
    task = await registry.tasks.claim_task(
        team_run_id=team_run_id,
        task_id=task_id,
        agent_name=agent_name,
    )
    if task is None:
        return f"Could not claim task '{task_id}' — already claimed, not found, or dependencies not met."
    return f"Claimed task '{task_id}': {task.title}"


async def task_update(
    team_run_id: str,
    task_id: str,
    status: str = "",
    result: str = "",
) -> str:
    task = await registry.tasks.update_task(
        team_run_id=team_run_id,
        task_id=task_id,
        status=status or None,
        result=result or None,
    )
    if task is None:
        return f"Task '{task_id}' not found."
    return f"Updated task '{task_id}' — status={task.status}"


async def task_add_note(team_run_id: str, task_id: str, agent_name: str, content: str) -> str:
    from agentic_primitives_gateway.models.tasks import TaskNote

    note = TaskNote(agent=agent_name, content=content)
    task = await registry.tasks.add_note(team_run_id=team_run_id, task_id=task_id, note=note)
    if task is None:
        return f"Task '{task_id}' not found."
    return f"Added note to task '{task_id}'."


async def task_get_available(team_run_id: str, agent_name: str = "") -> str:
    tasks = await registry.tasks.get_available(team_run_id=team_run_id, worker_name=agent_name or None)
    if not tasks:
        return "No available tasks (all tasks are done, claimed, or have unmet dependencies)."
    lines = [f"- [{t.id}] p{t.priority}: {t.title}" for t in tasks]
    return "\n".join(lines)


# ── Agent management ─────────────────────────────────────────────


async def agent_create(
    agent_store: Any,
    name: str,
    model: str,
    system_prompt: str = "You are a helpful assistant.",
    description: str = "",
    primitives: str = "{}",
) -> str:
    """Create a new agent spec in the store.

    ``primitives`` is a JSON string mapping primitive names to config,
    e.g. '{"memory": {"enabled": true}, "browser": {"enabled": true}}'
    """
    from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig

    try:
        prim_raw = json.loads(primitives) if primitives else {}
    except json.JSONDecodeError:
        return f"Error: invalid primitives JSON: {primitives[:200]}"

    prim_configs = {}
    for prim_name, prim_val in prim_raw.items():
        if isinstance(prim_val, dict):
            prim_configs[prim_name] = PrimitiveConfig(**prim_val)
        elif isinstance(prim_val, bool) and prim_val:
            prim_configs[prim_name] = PrimitiveConfig(enabled=True)

    spec = AgentSpec(
        name=name,
        model=model,
        system_prompt=system_prompt,
        description=description,
        primitives=prim_configs,
    )

    existing = await agent_store.get(name)
    if existing is not None:
        return f"Agent '{name}' already exists. Use a different name."

    await agent_store.create(spec)
    enabled = [p for p, c in prim_configs.items() if c.enabled]
    return f"Created agent '{name}' with primitives: {enabled or ['none']}"


async def agent_list(agent_store: Any) -> str:
    """List all agents with their descriptions and capabilities."""
    agents = await agent_store.list()
    if not agents:
        return "No agents exist."
    lines = []
    for a in agents:
        prims = [p for p, c in a.primitives.items() if c.enabled]
        lines.append(f"- {a.name}: {a.description or '(no description)'} [{', '.join(prims) or 'no primitives'}]")
    return "\n".join(lines)


async def agent_list_primitives() -> str:
    """List available primitives and their tools so the agent knows what capabilities exist."""
    from agentic_primitives_gateway.agents.tools.catalog import _TOOL_CATALOG

    lines = ["Available primitives and tools:"]
    for prim_name, tools in _TOOL_CATALOG.items():
        tool_names = [t.name for t in tools]
        lines.append(f"  {prim_name}: {', '.join(tool_names)}")
    lines.append("  agents: delegate_to (call any agent by name)")
    return "\n".join(lines)


async def agent_delete(agent_store: Any, name: str) -> str:
    """Delete an agent from the store."""
    deleted = await agent_store.delete(name)
    if not deleted:
        return f"Agent '{name}' not found."
    return f"Deleted agent '{name}'."


async def agent_delegate_to(
    agent_store: Any,
    agent_runner: Any,
    depth: int,
    agent_name: str,
    message: str,
) -> str:
    """Delegate a task to any agent by name. The agent runs its full tool-call loop."""
    spec = await agent_store.get(agent_name)
    if spec is None:
        return f"Agent '{agent_name}' not found. Use agent_list to see available agents, or create_agent to make one."
    try:
        response = await agent_runner.run(spec, message=message, _depth=depth + 1)
        parts = [response.response]
        if response.artifacts:
            parts.append("\n\n--- Tool Artifacts ---")
            for artifact in response.artifacts:
                parts.append(f"\n[{artifact.tool_name}]")
                if artifact.tool_input:
                    code = artifact.tool_input.get("code", "")
                    if code:
                        parts.append(f"```\n{code}\n```")
                if artifact.output:
                    parts.append(f"Output: {artifact.output}")
        return "\n".join(parts)
    except Exception as e:
        return f"Agent '{agent_name}' failed: {type(e).__name__}: {e}"
