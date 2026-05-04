"""Tool handler functions for each primitive.

Each handler wraps a registry method call and returns a string suitable
for inclusion in an LLM conversation as a tool result.

Handlers read request-scoped context (memory namespace, session ID,
team run ID, etc.) from per-primitive contextvar modules
(``primitives/<p>/context.py``) rather than receiving it as params.
This keeps the tool-catalog dispatch free of per-primitive special
cases and makes each primitive responsible for its own context shape.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig
from agentic_primitives_gateway.models.tasks import TaskNote
from agentic_primitives_gateway.primitives.browser.context import get_browser_session_id
from agentic_primitives_gateway.primitives.code_interpreter.context import get_code_interpreter_session_id
from agentic_primitives_gateway.primitives.knowledge.context import (
    claim_citation_indices,
    get_knowledge_inline_citations,
    get_knowledge_namespace,
)
from agentic_primitives_gateway.primitives.memory.context import (
    get_memory_namespace,
    get_memory_pools,
    get_shared_memory_namespace,
)
from agentic_primitives_gateway.primitives.tasks.context import get_agent_role, get_team_run_id
from agentic_primitives_gateway.registry import registry

logger = logging.getLogger(__name__)


# ── Context accessors — consolidate "required" checks so handlers stay terse ──


def _require_memory_namespace() -> str:
    ns = get_memory_namespace()
    if ns is None:
        raise RuntimeError("memory tool called outside a run — no memory namespace bound in context")
    return ns


def _require_shared_memory_namespace() -> str:
    ns = get_shared_memory_namespace()
    if ns is None:
        raise RuntimeError("shared_memory tool called without a team shared namespace bound")
    return ns


def _require_knowledge_namespace() -> str:
    ns = get_knowledge_namespace()
    if ns is None:
        # The tool should have been dropped from build_tool_list when no
        # corpus resolved — hitting this means the runner didn't set the
        # contextvar.  Memory namespace is NEVER a valid fallback.
        raise RuntimeError(
            "knowledge tool called without a bound corpus namespace — "
            "set primitives.knowledge.namespace on the agent spec"
        )
    return ns


def _require_browser_session() -> str:
    sid = get_browser_session_id()
    if sid is None:
        raise RuntimeError("browser tool called without an active session — session start failed or was skipped")
    return sid


def _require_code_interpreter_session() -> str:
    sid = get_code_interpreter_session_id()
    if sid is None:
        raise RuntimeError("code_interpreter tool called without an active session")
    return sid


def _require_team_run_id() -> str:
    rid = get_team_run_id()
    if rid is None:
        raise RuntimeError("task_board tool called outside a team run")
    return rid


def _require_agent_role() -> str:
    role = get_agent_role()
    if role is None:
        raise RuntimeError("task_board tool requires agent_role in context (planner/synthesizer/worker name)")
    return role


# ── Memory ───────────────────────────────────────────────────────────


async def memory_store(key: str, content: str, source: str = "") -> str:
    namespace = _require_memory_namespace()
    metadata = {"source": source} if source else {}
    await registry.memory.store(namespace=namespace, key=key, content=content, metadata=metadata)
    return f"Stored memory '{key}'."


async def memory_retrieve(key: str) -> str:
    namespace = _require_memory_namespace()
    record = await registry.memory.retrieve(namespace=namespace, key=key)
    if record is None:
        return f"No memory found for key '{key}'."
    return record.content


async def memory_search(query: str, top_k: int = 5) -> str:
    namespace = _require_memory_namespace()
    results = await registry.memory.search(namespace=namespace, query=query, top_k=top_k)
    if not results:
        return "No memories found."
    return "\n".join(f"- [{r.score:.2f}] {r.record.key}: {r.record.content}" for r in results)


async def memory_delete(key: str) -> str:
    namespace = _require_memory_namespace()
    deleted = await registry.memory.delete(namespace=namespace, key=key)
    return f"Deleted: {deleted}"


async def memory_list(limit: int = 20) -> str:
    namespace = _require_memory_namespace()
    records = await registry.memory.list_memories(namespace=namespace, limit=limit)
    if not records:
        return "No memories found."
    return "\n".join(f"- {r.key}: {r.content[:100]}" for r in records)


# ── Knowledge (RAG / retrieval) ──────────────────────────────────────


async def knowledge_search(query: str, top_k: int = 5, include_sources: bool = False) -> str:
    """Retrieve chunks from the agent's knowledge base.

    ``include_sources`` is a per-call opt-in for rich citations: when
    ``True`` the handler asks the provider to populate
    ``RetrievedChunk.citations`` and attaches the structured chunks to
    the surrounding ``ToolArtifact.structured`` sideband.

    **Inline-citations mode** (agent-spec opt-in via the
    ``knowledge_inline_citations`` contextvar, set by the runner from
    ``primitives.knowledge.inline_citations`` on the spec): the handler
    prepends each chunk with a globally-unique ``[N]`` marker and
    instructs the LLM — via the tool result text — to cite claims with
    those markers.  The UI renders ``[N]`` as pills linked to the
    structured chunks in the artifact panel.  Inline mode implies
    ``include_sources=True`` so the structured payload is always
    available for the UI to resolve markers against.
    """
    namespace = _require_knowledge_namespace()
    inline = get_knowledge_inline_citations()
    want_structured = include_sources or inline

    chunks = await registry.knowledge.retrieve(
        namespace=namespace,
        query=query,
        top_k=top_k,
        include_citations=want_structured,
    )

    # In inline mode we reserve a contiguous range of citation indices
    # up-front so the marker on each chunk is stable between the LLM
    # text and the structured payload the UI resolves against.
    base_index = claim_citation_indices(len(chunks)) if inline else 0

    if want_structured:
        from agentic_primitives_gateway.agents.tools.context import set_current_artifact_structured

        structured_chunks: list[dict[str, Any]] = []
        for offset, c in enumerate(chunks):
            entry = c.model_dump(exclude_none=True)
            if inline:
                entry["citation_index"] = base_index + offset
            structured_chunks.append(entry)

        set_current_artifact_structured(
            {
                "kind": "knowledge_search",
                "query": query,
                "namespace": namespace,
                "chunks": structured_chunks,
                "inline": inline,
            }
        )

    if not chunks:
        return "No relevant knowledge found."

    lines: list[str] = []
    if inline:
        # Give the LLM explicit instructions so it uses the markers.
        # This lives in the tool result text (not the system prompt) so
        # it's scoped to this specific call — agents that don't enable
        # inline citations never see it.
        lines.append(
            "Each result is tagged with a [N] marker. When you cite information from a result, "
            "write the [N] marker immediately after the claim so the UI can link it back to the source."
        )
    for offset, c in enumerate(chunks):
        source = c.metadata.get("source") if isinstance(c.metadata, dict) else None
        prefix = ""
        if inline:
            prefix += f"[{base_index + offset}] "
        prefix += f"[{c.score:.2f}] "
        if source:
            prefix += f"({source}) "
        lines.append(f"- {prefix}{c.text}")
    return "\n".join(lines)


# ── Shared memory (team-scoped) ────────────────────────────────────


async def shared_memory_store(key: str, content: str, source: str = "") -> str:
    shared_ns = _require_shared_memory_namespace()
    metadata = {"source": source} if source else {}
    await registry.memory.store(namespace=shared_ns, key=key, content=content, metadata=metadata)
    return f"Shared finding '{key}' with the team."


async def shared_memory_retrieve(key: str) -> str:
    shared_ns = _require_shared_memory_namespace()
    record = await registry.memory.retrieve(namespace=shared_ns, key=key)
    if record is None:
        return f"No shared finding found for key '{key}'."
    return record.content


async def shared_memory_search(query: str, top_k: int = 5) -> str:
    shared_ns = _require_shared_memory_namespace()
    results = await registry.memory.search(namespace=shared_ns, query=query, top_k=top_k)
    if not results:
        return "No shared findings found."
    return "\n".join(f"- [{r.score:.2f}] {r.record.key}: {r.record.content}" for r in results)


async def shared_memory_list(limit: int = 20) -> str:
    shared_ns = _require_shared_memory_namespace()
    records = await registry.memory.list_memories(namespace=shared_ns, limit=limit)
    if not records:
        return "No shared findings."
    return "\n".join(f"- {r.key}: {r.content[:100]}" for r in records)


# ── Pool-based shared memory (agent-level, multiple namespaces) ─────


def _resolve_pool(pool: str) -> str:
    """Resolve a pool name to its namespace. Raises ValueError if invalid."""
    pools = get_memory_pools() or {}
    ns = pools.get(pool)
    if ns is None:
        available = ", ".join(sorted(pools.keys())) or "(none configured)"
        raise ValueError(f"Unknown pool '{pool}'. Available pools: {available}")
    return ns


async def pool_memory_store(pool: str, key: str, content: str) -> str:
    ns = _resolve_pool(pool)
    await registry.memory.store(namespace=ns, key=key, content=content, metadata={})
    return f"Stored '{key}' in pool '{pool}'."


async def pool_memory_retrieve(pool: str, key: str) -> str:
    ns = _resolve_pool(pool)
    record = await registry.memory.retrieve(namespace=ns, key=key)
    if record is None:
        return f"No finding for key '{key}' in pool '{pool}'."
    return record.content


async def pool_memory_search(pool: str, query: str, top_k: int = 5) -> str:
    ns = _resolve_pool(pool)
    results = await registry.memory.search(namespace=ns, query=query, top_k=top_k)
    if not results:
        return f"No findings in pool '{pool}'."
    return "\n".join(f"- [{r.score:.2f}] {r.record.key}: {r.record.content}" for r in results)


async def pool_memory_list(pool: str, limit: int = 20) -> str:
    ns = _resolve_pool(pool)
    records = await registry.memory.list_memories(namespace=ns, limit=limit)
    if not records:
        return f"No findings in pool '{pool}'."
    return "\n".join(f"- {r.key}: {r.content[:100]}" for r in records)


# ── Code interpreter ────────────────────────────────────────────────


async def code_execute(code: str, language: str = "python") -> str:
    session_id = _require_code_interpreter_session()
    result = await registry.code_interpreter.execute(session_id=session_id, code=code, language=language)
    return json.dumps(result, default=str)


# ── Browser ─────────────────────────────────────────────────────────


async def browser_navigate(url: str) -> str:
    session_id = _require_browser_session()
    result = await registry.browser.navigate(session_id=session_id, url=url)
    return json.dumps(result, default=str)


async def browser_read_page() -> str:
    session_id = _require_browser_session()
    return await registry.browser.get_page_content(session_id=session_id)


async def browser_click(selector: str) -> str:
    session_id = _require_browser_session()
    result = await registry.browser.click(session_id=session_id, selector=selector)
    return json.dumps(result, default=str)


async def browser_type(selector: str, text: str) -> str:
    session_id = _require_browser_session()
    result = await registry.browser.type_text(session_id=session_id, selector=selector, text=text)
    return json.dumps(result, default=str)


async def browser_screenshot() -> str:
    session_id = _require_browser_session()
    result = await registry.browser.screenshot(session_id=session_id)
    return f"Screenshot captured ({len(result)} bytes). Use read_page to see text content instead."


async def browser_evaluate_js(expression: str) -> str:
    session_id = _require_browser_session()
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
    title: str,
    description: str = "",
    depends_on: str = "",
    priority: int = 0,
    assigned_to: str = "",
) -> str:
    team_run_id = _require_team_run_id()
    agent_role = _require_agent_role()
    deps = [d.strip() for d in depends_on.split(",") if d.strip()] if depends_on else []
    task = await registry.tasks.create_task(
        team_run_id=team_run_id,
        title=title,
        description=description,
        created_by=agent_role,
        depends_on=deps,
        priority=priority,
        suggested_worker=assigned_to or None,
    )
    emit_audit_event(
        action=AuditAction.TASK_CREATE,
        outcome=AuditOutcome.SUCCESS,
        resource_type=ResourceType.TASK,
        resource_id=f"{team_run_id}/{task.id}",
        metadata={
            "created_by": agent_role,
            "depends_on": deps,
            "priority": priority,
            "suggested_worker": assigned_to or None,
        },
    )
    return json.dumps(
        {"id": task.id, "title": task.title, "status": task.status, "assigned_to": task.suggested_worker}, default=str
    )


async def task_list(status: str = "", assigned_to: str = "") -> str:
    team_run_id = _require_team_run_id()
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


async def task_get(task_id: str) -> str:
    team_run_id = _require_team_run_id()
    task = await registry.tasks.get_task(team_run_id=team_run_id, task_id=task_id)
    if task is None:
        return f"Task '{task_id}' not found."
    return json.dumps(task.model_dump(), default=str)


async def task_claim(task_id: str) -> str:
    team_run_id = _require_team_run_id()
    agent_role = _require_agent_role()
    task = await registry.tasks.claim_task(
        team_run_id=team_run_id,
        task_id=task_id,
        agent_name=agent_role,
    )
    emit_audit_event(
        action=AuditAction.TASK_CLAIM,
        outcome=AuditOutcome.SUCCESS if task is not None else AuditOutcome.FAILURE,
        resource_type=ResourceType.TASK,
        resource_id=f"{team_run_id}/{task_id}",
        metadata={"claimed_by": agent_role},
    )
    if task is None:
        return f"Could not claim task '{task_id}' — already claimed, not found, or dependencies not met."
    return f"Claimed task '{task_id}': {task.title}"


async def task_update(
    task_id: str,
    status: str = "",
    result: str = "",
) -> str:
    team_run_id = _require_team_run_id()
    task = await registry.tasks.update_task(
        team_run_id=team_run_id,
        task_id=task_id,
        status=status or None,
        result=result or None,
    )
    emit_audit_event(
        action=AuditAction.TASK_UPDATE,
        outcome=AuditOutcome.SUCCESS if task is not None else AuditOutcome.FAILURE,
        resource_type=ResourceType.TASK,
        resource_id=f"{team_run_id}/{task_id}",
        metadata={"status": status or None, "has_result": bool(result)},
    )
    if task is None:
        return f"Task '{task_id}' not found."
    return f"Updated task '{task_id}' — status={task.status}"


async def task_add_note(task_id: str, content: str) -> str:
    team_run_id = _require_team_run_id()
    agent_role = _require_agent_role()
    note = TaskNote(agent=agent_role, content=content)
    task = await registry.tasks.add_note(team_run_id=team_run_id, task_id=task_id, note=note)
    emit_audit_event(
        action=AuditAction.TASK_NOTE,
        outcome=AuditOutcome.SUCCESS if task is not None else AuditOutcome.FAILURE,
        resource_type=ResourceType.TASK,
        resource_id=f"{team_run_id}/{task_id}",
        metadata={"author": agent_role},
    )
    if task is None:
        return f"Task '{task_id}' not found."
    return f"Added note to task '{task_id}'."


async def task_get_available() -> str:
    team_run_id = _require_team_run_id()
    agent_role = _require_agent_role()
    tasks = await registry.tasks.get_available(team_run_id=team_run_id, worker_name=agent_role or None)
    if not tasks:
        return "No available tasks (all tasks are done, claimed, or have unmet dependencies)."
    lines = [f"- [{t.id}] p{t.priority}: {t.title}" for t in tasks]
    return "\n".join(lines)


# ── Agent management ─────────────────────────────────────────────
# Delegation tools stay param-driven — agent_store / agent_runner / depth
# are call-stack state for the parent runner, not request-scoped state.


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
