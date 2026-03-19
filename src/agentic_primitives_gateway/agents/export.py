"""Export declarative agent/team specs as standalone Python code.

Generates Python scripts that use ``agentic-primitives-gateway-client``
for primitive calls (memory, browser, code interpreter, observability)
and raw ``boto3`` Bedrock ``converse()`` for the LLM loop.  The user gets
full control over the loop logic while the gateway still handles provider
abstraction, credentials, and multi-tenancy.
"""

from __future__ import annotations

import json
from typing import Any

from agentic_primitives_gateway.agents.tools.catalog import _TOOL_CATALOG
from agentic_primitives_gateway.models.agents import AgentSpec, PrimitiveConfig
from agentic_primitives_gateway.models.teams import TeamSpec

# ── Shared helpers ───────────────────────────────────────────────────


def _tools_for_primitives(primitives: dict[str, PrimitiveConfig]) -> list[dict[str, Any]]:
    """Build tool definitions from enabled primitives (excluding agents)."""
    tools: list[dict[str, Any]] = []
    for prim_name, prim_cfg in primitives.items():
        if not prim_cfg.enabled or prim_name in ("agents",):
            continue
        if prim_name not in _TOOL_CATALOG:
            continue
        allowed = set(prim_cfg.tools) if prim_cfg.tools else None
        for td in _TOOL_CATALOG[prim_name]:
            if allowed and td.name not in allowed:
                continue
            tools.append(
                {
                    "name": td.name,
                    "description": td.description,
                    "input_schema": td.input_schema,
                }
            )
    return tools


def _enabled_primitives(primitives: dict[str, PrimitiveConfig]) -> list[str]:
    return [k for k, v in primitives.items() if v.enabled and k != "agents"]


def _extract_region(model_id: str) -> str:
    if model_id.startswith("us."):
        return "us-east-1"
    if model_id.startswith("eu."):
        return "eu-west-1"
    if model_id.startswith("ap."):
        return "ap-northeast-1"
    return "us-east-1"


def _escape_triple_quotes(s: str) -> str:
    return s.replace('"""', '\\"\\"\\"')


# ── Dispatch case builder ────────────────────────────────────────────


def _build_dispatch_cases(
    prims: list[str],
    *,
    namespace_var: str = "NAMESPACE",
    sub_agents: list[str] | None = None,
    shared_pools: list[str] | None = None,
    shared_ns_var: str | None = None,
) -> str:
    """Generate the tool dispatch if/elif chain."""
    lines: list[str] = []

    if "memory" in prims:
        lines.extend(
            [
                '    if name == "remember":',
                f'        r = await client.store_memory({namespace_var}, tool_input["key"], tool_input["content"])',
                "        return f\"Stored: {tool_input['key']}\"",
                '    elif name == "recall":',
                f'        r = await client.retrieve_memory({namespace_var}, tool_input["key"])',
                '        return r.get("content", "Not found") if r else "Not found"',
                '    elif name == "search_memory":',
                f'        results = await client.search_memory({namespace_var}, tool_input["query"], top_k=tool_input.get("top_k", 5))',
                '        return json.dumps(results.get("results", []), indent=2)',
                '    elif name == "forget":',
                f'        await client.delete_memory({namespace_var}, tool_input["key"])',
                "        return f\"Deleted: {tool_input['key']}\"",
                '    elif name == "list_memories":',
                f'        r = await client.list_memories({namespace_var}, limit=tool_input.get("limit", 20))',
                '        return json.dumps(r.get("records", []), indent=2)',
            ]
        )
    if "browser" in prims:
        prefix = "elif" if lines else "if"
        lines.extend(
            [
                f'    {prefix} name == "navigate":',
                '        sid = await _ensure_session("browser", _sessions_ctx)',
                '        r = await client.browser_navigate(sid, tool_input["url"])',
                "        return json.dumps(r)",
                '    elif name == "read_page":',
                '        sid = await _ensure_session("browser", _sessions_ctx)',
                "        r = await client.browser_get_content(sid)",
                '        return r.get("content", "")',
                '    elif name == "click":',
                '        sid = await _ensure_session("browser", _sessions_ctx)',
                '        r = await client.browser_click(sid, tool_input["selector"])',
                "        return json.dumps(r)",
                '    elif name == "type_text":',
                '        sid = await _ensure_session("browser", _sessions_ctx)',
                '        r = await client.browser_type(sid, tool_input["selector"], tool_input["text"])',
                "        return json.dumps(r)",
                '    elif name == "screenshot":',
                '        sid = await _ensure_session("browser", _sessions_ctx)',
                "        r = await client.browser_screenshot(sid)",
                '        return "Screenshot taken"',
                '    elif name == "evaluate_js":',
                '        sid = await _ensure_session("browser", _sessions_ctx)',
                '        r = await client.browser_evaluate(sid, tool_input["expression"])',
                "        return json.dumps(r)",
            ]
        )
    if "code_interpreter" in prims:
        prefix = "elif" if lines else "if"
        lines.extend(
            [
                f'    {prefix} name == "execute_code":',
                '        sid = await _ensure_session("code_interpreter", _sessions_ctx)',
                '        r = await client.execute_code(sid, tool_input["code"], language=tool_input.get("language", "python"))',
                '        return r.get("output", "")',
            ]
        )
    if "tools" in prims:
        prefix = "elif" if lines else "if"
        lines.extend(
            [
                f'    {prefix} name == "search_tools":',
                '        r = await client.search_tools(tool_input["query"], max_results=tool_input.get("max_results", 10))',
                "        return json.dumps(r, indent=2)",
                '    elif name == "invoke_tool":',
                '        params = json.loads(tool_input.get("params", "{}"))',
                '        r = await client.invoke_tool(tool_input["tool_name"], params)',
                "        return json.dumps(r)",
            ]
        )

    # Shared memory (team-scoped, single namespace)
    if shared_ns_var:
        prefix = "elif" if lines else "if"
        lines.extend(
            [
                f'    {prefix} name == "share_finding":',
                f'        await client.store_memory({shared_ns_var}, tool_input["key"], tool_input["content"])',
                "        return f\"Shared: {tool_input['key']}\"",
                '    elif name == "read_shared":',
                f'        r = await client.retrieve_memory({shared_ns_var}, tool_input["key"])',
                '        return r.get("content", "Not found") if r else "Not found"',
                '    elif name == "search_shared":',
                f'        results = await client.search_memory({shared_ns_var}, tool_input["query"], top_k=tool_input.get("top_k", 5))',
                '        return json.dumps(results.get("results", []), indent=2)',
                '    elif name == "list_shared":',
                f'        r = await client.list_memories({shared_ns_var}, limit=tool_input.get("limit", 20))',
                '        return json.dumps(r.get("records", []), indent=2)',
            ]
        )

    # Shared memory pools (agent-level, multiple namespaces)
    if shared_pools:
        prefix = "elif" if lines else "if"
        lines.extend(
            [
                f'    {prefix} name == "share_to":',
                '        ns = SHARED_POOLS.get(tool_input["pool"])',
                "        if not ns: return f\"Unknown pool: {tool_input['pool']}\"",
                '        await client.store_memory(ns, tool_input["key"], tool_input["content"])',
                "        return f\"Stored in {tool_input['pool']}: {tool_input['key']}\"",
                '    elif name == "search_pool":',
                '        ns = SHARED_POOLS.get(tool_input["pool"])',
                "        if not ns: return f\"Unknown pool: {tool_input['pool']}\"",
                '        results = await client.search_memory(ns, tool_input["query"], top_k=tool_input.get("top_k", 5))',
                '        return json.dumps(results.get("results", []), indent=2)',
                '    elif name == "read_from_pool":',
                '        ns = SHARED_POOLS.get(tool_input["pool"])',
                "        if not ns: return f\"Unknown pool: {tool_input['pool']}\"",
                '        r = await client.retrieve_memory(ns, tool_input["key"])',
                '        return r.get("content", "Not found") if r else "Not found"',
                '    elif name == "list_pool":',
                '        ns = SHARED_POOLS.get(tool_input["pool"])',
                "        if not ns: return f\"Unknown pool: {tool_input['pool']}\"",
                '        r = await client.list_memories(ns, limit=tool_input.get("limit", 20))',
                '        return json.dumps(r.get("records", []), indent=2)',
            ]
        )

    # Sub-agent delegation
    if sub_agents:
        for sa in sub_agents:
            prefix = "elif" if lines else "if"
            lines.extend(
                [
                    f'    {prefix} name == "call_{sa}":',
                    f'        return await run_agent("{sa}", tool_input.get("message", ""))',
                ]
            )

    if not lines:
        return "    pass  # No primitives enabled\n"

    return "\n".join(lines) + "\n"


# ── Token refresh code block ──────────────────────────────────────────

_TOKEN_REFRESH = '''
# ── Token auto-refresh ────────────────────────────────────────────

import time as _time

_token_expiry: float = 0


def _decode_exp(token: str) -> float:
    """Extract expiry timestamp from JWT without validation."""
    import base64
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)  # pad base64
        data = json.loads(base64.urlsafe_b64decode(payload))
        return float(data.get("exp", 0))
    except Exception:
        return 0


def refresh_token_if_needed():
    """Re-fetch the JWT if it's expired or about to expire (30s buffer)."""
    global _token_expiry
    if _token_expiry == 0:
        return  # no token configured
    if _time.time() < _token_expiry - 30:
        return  # still valid
    token = fetch_token_from_env(verbose=False)
    if token:
        client.set_auth_token(token)
        _token_expiry = _decode_exp(token)


def _init_token():
    """Set initial token and track expiry."""
    global _token_expiry
    token = fetch_token_from_env()
    if token:
        client.set_auth_token(token)
        _token_expiry = _decode_exp(token)


_init_token()
'''

# ── Session management code block ────────────────────────────────────

_SESSION_MGMT = '''
# ── Session management ────────────────────────────────────────────

_sessions: dict[str, str] = {}  # primitive -> session_id (for single-agent use)


async def _ensure_session(primitive: str, sessions: dict[str, str] | None = None) -> str:
    """Start a session lazily on first use, return the session_id.

    When ``sessions`` is provided (team parallel execution), uses that
    dict for per-task isolation. Otherwise uses the global ``_sessions``.
    """
    store = sessions if sessions is not None else _sessions
    if primitive in store:
        return store[primitive]
    if primitive == "browser":
        r = await client.start_browser_session()
        store["browser"] = r["session_id"]
    elif primitive == "code_interpreter":
        r = await client.start_code_session()
        store["code_interpreter"] = r["session_id"]
    return store[primitive]


async def _cleanup_sessions(sessions: dict[str, str] | None = None):
    """Stop all active sessions."""
    refresh_token_if_needed()
    store = sessions if sessions is not None else _sessions
    for prim, sid in store.items():
        try:
            if prim == "browser":
                await client.stop_browser_session(sid)
            elif prim == "code_interpreter":
                await client.stop_code_session(sid)
        except Exception:
            pass
    store.clear()
'''

# ── LLM loop code block ─────────────────────────────────────────────


_LLM_LOOP = '''
async def run_llm_loop(model_id: str, system_prompt: str, message: str,
                       tools: list, tool_config: dict, max_turns: int,
                       temperature: float, execute_fn, region: str,
                       on_tool: callable = None) -> str:
    """Generic LLM tool-call loop using Bedrock converse().

    Tool calls within a turn run in parallel via asyncio.gather.
    ``on_tool`` is an optional callback(name, input) for UI updates.
    """
    bedrock = boto3.client("bedrock-runtime", region_name=region)
    messages = [{"role": "user", "content": [{"text": message}]}]

    for turn in range(max_turns):
        refresh_token_if_needed()

        kwargs = {
            "modelId": model_id,
            "messages": messages,
            "system": [{"text": system_prompt}],
            "inferenceConfig": {"temperature": temperature},
        }
        if tools:
            kwargs["toolConfig"] = tool_config

        response = bedrock.converse(**kwargs)
        output = response["output"]["message"]
        messages.append(output)

        if response["stopReason"] != "tool_use":
            return "".join(
                block["text"] for block in output["content"] if "text" in block
            )

        # Execute tool calls in parallel
        tool_uses = [b["toolUse"] for b in output["content"] if "toolUse" in b]

        async def _exec_one(tu):
            refresh_token_if_needed()
            if on_tool:
                on_tool(tu["name"], tu["input"])
            result = await execute_fn(tu["name"], tu["input"])
            return {
                "toolResult": {
                    "toolUseId": tu["toolUseId"],
                    "content": [{"text": result}],
                }
            }

        tool_results = await asyncio.gather(*[_exec_one(tu) for tu in tool_uses])
        messages.append({"role": "user", "content": list(tool_results)})

    return "(max turns reached)"
'''


# ── Agent export ─────────────────────────────────────────────────────


def export_agent(spec: AgentSpec, all_specs: dict[str, AgentSpec] | None = None) -> str:
    """Generate a standalone Python script from an agent spec."""
    prims = _enabled_primitives(spec.primitives)
    tools = _tools_for_primitives(spec.primitives)

    # Sub-agent delegation
    agents_cfg = spec.primitives.get("agents")
    sub_agents = agents_cfg.tools if agents_cfg and agents_cfg.enabled and agents_cfg.tools else []

    # Add call_X tools for sub-agents
    for sa in sub_agents:
        tools.append(
            {
                "name": f"call_{sa}",
                "description": f"Delegate a task to the {sa} agent.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": f"The message/task to send to {sa}."},
                    },
                    "required": ["message"],
                },
            }
        )

    # Shared memory pools (Level 2)
    mem_cfg = spec.primitives.get("memory")
    shared_pools = mem_cfg.shared_namespaces if mem_cfg and mem_cfg.shared_namespaces else None

    if shared_pools:
        pool_names = ", ".join(shared_pools)
        for tool_name, desc, schema in [
            (
                "share_to",
                f"Store a finding in a shared pool. Pools: {pool_names}",
                {
                    "type": "object",
                    "properties": {
                        "pool": {"type": "string", "description": f"Pool name. Available: {pool_names}"},
                        "key": {"type": "string", "description": "Finding identifier."},
                        "content": {"type": "string", "description": "The information to share."},
                    },
                    "required": ["pool", "key", "content"],
                },
            ),
            (
                "search_pool",
                f"Search a shared pool. Pools: {pool_names}",
                {
                    "type": "object",
                    "properties": {
                        "pool": {"type": "string", "description": f"Pool name. Available: {pool_names}"},
                        "query": {"type": "string", "description": "Search query."},
                    },
                    "required": ["pool", "query"],
                },
            ),
            (
                "read_from_pool",
                f"Read from a shared pool. Pools: {pool_names}",
                {
                    "type": "object",
                    "properties": {
                        "pool": {"type": "string", "description": f"Pool name. Available: {pool_names}"},
                        "key": {"type": "string", "description": "Key to look up."},
                    },
                    "required": ["pool", "key"],
                },
            ),
            (
                "list_pool",
                f"List findings in a shared pool. Pools: {pool_names}",
                {
                    "type": "object",
                    "properties": {
                        "pool": {"type": "string", "description": f"Pool name. Available: {pool_names}"},
                    },
                    "required": ["pool"],
                },
            ),
        ]:
            tools.append({"name": tool_name, "description": desc, "input_schema": schema})

    tools_json = json.dumps(tools, indent=2)
    dispatch = _build_dispatch_cases(prims, sub_agents=sub_agents, shared_pools=shared_pools)
    system_prompt = _escape_triple_quotes(spec.system_prompt)
    region = _extract_region(spec.model)

    # Build sub-agent functions
    sub_agent_code = ""
    if sub_agents and all_specs:
        sub_agent_code = _build_sub_agent_code(sub_agents, all_specs, region)

    # Shared pools config
    pools_config = ""
    if shared_pools:
        pool_dict = {p: f"{p}:u:{{USER_ID}}" for p in shared_pools}
        pools_config = f"\nSHARED_POOLS = {json.dumps(pool_dict, indent=2)}\n"
        pools_config += '\nUSER_ID = "default"  # Set from auth context\n'

    needs_sessions = "browser" in prims or "code_interpreter" in prims
    session_block = _SESSION_MGMT if needs_sessions else ""
    cleanup_call = "\n    await _cleanup_sessions()" if needs_sessions else ""

    return f'''#!/usr/bin/env python3
"""Exported agent: {spec.name}

Generated from declarative agent spec. Uses the gateway client for
primitive calls and boto3 Bedrock converse() for the LLM loop.

Usage:
    pip install agentic-primitives-gateway-client[aws] rich  # rich is optional
    export GATEWAY_URL=http://localhost:8000
    export JWT_TOKEN=<your-jwt>  # or set OIDC_ISSUER + OIDC_USERNAME + OIDC_PASSWORD
    python {spec.name}.py
"""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import boto3

from agentic_primitives_gateway_client import AgenticPlatformClient, fetch_token_from_env

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    console = None

# ── Configuration ─────────────────────────────────────────────────

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
MODEL_ID = "{spec.model}"
MAX_TURNS = {spec.max_turns}
TEMPERATURE = {spec.temperature}
NAMESPACE = "agent:{spec.name}"
REGION = "{region}"
SESSION_ID = f"session-{{uuid4().hex[:8]}}"

SYSTEM_PROMPT = """{system_prompt}"""

# ── Gateway client ────────────────────────────────────────────────
#
# Authentication and credential resolution:
#   1. JWT auth via fetch_token_from_env() — checks JWT_TOKEN env var,
#      then OIDC_* or KEYCLOAK_* env vars for automatic token fetch.
#      The gateway resolves per-user credentials (apg.* attributes)
#      from the JWT automatically via credential resolution middleware.
#   2. If no per-user credentials are found, the gateway falls back to
#      server-side ambient credentials (if allow_server_credentials != never).
#   3. You can also pass credentials explicitly via set_service_credentials()
#      or set_aws_credentials() — these take highest priority.

client = AgenticPlatformClient(GATEWAY_URL, aws_from_environment=True)
{_TOKEN_REFRESH}
{pools_config}{session_block}
# ── Tools ─────────────────────────────────────────────────────────

TOOLS = {tools_json}

TOOL_CONFIG = {{
    "tools": [{{"toolSpec": {{
        "name": t["name"],
        "description": t["description"],
        "inputSchema": {{"json": t["input_schema"]}},
    }}}} for t in TOOLS]
}}

# ── LLM loop ─────────────────────────────────────────────────────
{_LLM_LOOP}
# ── Tool dispatch ─────────────────────────────────────────────────


async def execute_tool(name: str, tool_input: dict, _sessions_ctx=None) -> str:
    """Route a tool call to the appropriate gateway primitive."""
{dispatch}    return f"Unknown tool: {{name}}"

{sub_agent_code}
# ── Run ───────────────────────────────────────────────────────────


def _on_tool(name: str, tool_input: dict) -> None:
    msg = f"  tool: {{name}}({{json.dumps(tool_input)[:80]}})"
    if HAS_RICH:
        console.print(f"  [dim]tool:[/dim] [cyan]{{name}}[/cyan]([dim]{{json.dumps(tool_input)[:80]}}[/dim])")
    else:
        print(msg)


async def run(message: str) -> str:
    return await run_llm_loop(
        MODEL_ID, SYSTEM_PROMPT, message, TOOLS, TOOL_CONFIG,
        MAX_TURNS, TEMPERATURE, execute_tool, REGION, on_tool=_on_tool,
    )


# ── Main ──────────────────────────────────────────────────────────


async def main():
    if HAS_RICH:
        console.print(Panel(
            f"[bold]{spec.name}[/bold]\\n"
            f"[dim]Model:[/dim] {{MODEL_ID}}\\n"
            f"[dim]Gateway:[/dim] {{GATEWAY_URL}}\\n"
            f"[dim]Session:[/dim] {{SESSION_ID}}",
            title="Agent", border_style="blue",
        ))
        console.print("[dim]Type 'quit' to exit.[/dim]\\n")
    else:
        print(f"Agent: {spec.name} | Model: {{MODEL_ID}} | Gateway: {{GATEWAY_URL}}")
        print("Type 'quit' to exit.\\n")

    while True:
        try:
            user_input = (console.input("[bold green]You:[/bold green] ") if HAS_RICH else input("You: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break
        print()
        response = await run(user_input)
        if HAS_RICH:
            console.print(Panel(Markdown(response), title="Assistant", border_style="green"))
        else:
            print(f"Assistant: {{response}}")
        print()
{cleanup_call}

if __name__ == "__main__":
    asyncio.run(main())
'''


def _build_sub_agent_code(sub_agents: list[str], all_specs: dict[str, AgentSpec], region: str) -> str:
    """Generate run_agent() function and per-agent configs for delegation."""
    lines = [
        "# ── Sub-agent configs ─────────────────────────────────────────────",
        "",
        "SUB_AGENTS = {",
    ]
    for sa in sub_agents:
        sa_spec = all_specs.get(sa)
        if sa_spec is None:
            continue
        sa_tools = _tools_for_primitives(sa_spec.primitives)
        sa_prompt = _escape_triple_quotes(sa_spec.system_prompt)
        lines.append(f'    "{sa}": {{')
        lines.append(f'        "model": "{sa_spec.model}",')
        lines.append(f'        "system_prompt": """{sa_prompt}""",')
        lines.append(f'        "max_turns": {sa_spec.max_turns},')
        lines.append(f'        "temperature": {sa_spec.temperature},')
        lines.append(f'        "tools": {json.dumps(sa_tools)},')
        lines.append("    },")
    lines.append("}")
    lines.append("")
    lines.append("")
    lines.append("async def run_agent(agent_name: str, message: str) -> str:")
    lines.append('    """Run a sub-agent by name."""')
    lines.append("    cfg = SUB_AGENTS.get(agent_name)")
    lines.append("    if cfg is None:")
    lines.append('        return f"Unknown agent: {agent_name}"')
    lines.append('    sa_tools = cfg["tools"]')
    lines.append("    sa_tool_config = {")
    lines.append('        "tools": [{"toolSpec": {')
    lines.append('            "name": t["name"],')
    lines.append('            "description": t["description"],')
    lines.append('            "inputSchema": {"json": t["input_schema"]},')
    lines.append("        }} for t in sa_tools]")
    lines.append("    }")
    lines.append('    ns = f"agent:{agent_name}"')
    lines.append("")
    lines.append("    async def sa_execute(name, tool_input):")
    lines.append("        # Sub-agents use the same dispatch but with their own namespace")
    lines.append("        return await execute_tool(name, tool_input)")
    lines.append("")
    lines.append("    return await run_llm_loop(")
    lines.append('        cfg["model"], cfg["system_prompt"], message,')
    lines.append('        sa_tools, sa_tool_config, cfg["max_turns"],')
    lines.append(f'        cfg["temperature"], sa_execute, "{region}",')
    lines.append("    )")
    lines.append("")
    return "\n".join(lines)


# ── Team export ──────────────────────────────────────────────────────


def export_team(
    team_spec: TeamSpec,
    agent_specs: dict[str, AgentSpec],
) -> str:
    """Generate a standalone Python script from a team spec."""
    region = _extract_region(
        agent_specs.get(team_spec.planner, AgentSpec(name="", model="us.anthropic.claude-sonnet-4-20250514-v1:0")).model
    )

    # Gather all worker info
    worker_configs = []
    for w in team_spec.workers:
        ws = agent_specs.get(w)
        if ws is None:
            continue
        prims = _enabled_primitives(ws.primitives)
        tools = _tools_for_primitives(ws.primitives)
        dispatch = _build_dispatch_cases(
            prims,
            namespace_var="namespace",
            shared_ns_var="shared_ns" if team_spec.shared_memory_namespace else None,
        )
        worker_configs.append(
            {
                "name": w,
                "model": ws.model,
                "system_prompt": _escape_triple_quotes(ws.system_prompt),
                "max_turns": ws.max_turns,
                "temperature": ws.temperature,
                "tools_json": json.dumps(tools, indent=2),
                "dispatch": dispatch,
                "prims": prims,
            }
        )

    # Planner and synthesizer specs
    planner = agent_specs.get(team_spec.planner)
    synth = agent_specs.get(team_spec.synthesizer)

    planner_prompt = _escape_triple_quotes(planner.system_prompt) if planner else "You are a task planner."
    planner_model = planner.model if planner else "us.anthropic.claude-sonnet-4-20250514-v1:0"
    synth_prompt = _escape_triple_quotes(synth.system_prompt) if synth else "You are a synthesizer."
    synth_model = synth.model if synth else "us.anthropic.claude-sonnet-4-20250514-v1:0"

    shared_ns_config = ""
    if team_spec.shared_memory_namespace:
        ns = team_spec.shared_memory_namespace.replace("{team_name}", team_spec.name)
        shared_ns_config = f'\nSHARED_NAMESPACE = "{ns}" + ":u:default"  # Set user ID from auth context\n'

    workers_dict = _build_workers_dict(worker_configs)
    worker_names = json.dumps(team_spec.workers)

    all_prims = _all_worker_prims(worker_configs)
    team_needs_sessions = "browser" in all_prims or "code_interpreter" in all_prims
    team_session_block = _SESSION_MGMT if team_needs_sessions else ""
    team_cleanup_call = "\n    await _cleanup_sessions()" if team_needs_sessions else ""

    # Pre-compute the worker dispatch code (can't nest function calls in f-strings).
    # _build_dispatch_cases already uses 4-space indent; add 4 more for nesting
    # inside the worker_execute function.
    worker_dispatch = _indent(
        _build_dispatch_cases(
            _all_worker_prims(worker_configs),
            namespace_var="namespace",
            shared_ns_var="shared_ns" if team_spec.shared_memory_namespace else None,
        ),
        4,
    )
    shared_ns_line = "shared_ns = SHARED_NAMESPACE" if team_spec.shared_memory_namespace else ""

    return f'''#!/usr/bin/env python3
"""Exported team: {team_spec.name}

Generated from declarative team spec. Uses the gateway client for
primitive calls and boto3 Bedrock converse() for the LLM loop.

Planner: {team_spec.planner}
Synthesizer: {team_spec.synthesizer}
Workers: {", ".join(team_spec.workers)}

Usage:
    pip install agentic-primitives-gateway-client[aws] rich  # rich is optional
    export GATEWAY_URL=http://localhost:8000
    export JWT_TOKEN=<your-jwt>  # or set OIDC_ISSUER + OIDC_USERNAME + OIDC_PASSWORD
    python {team_spec.name}.py
"""

from __future__ import annotations

import asyncio
import json
import os
from uuid import uuid4

import boto3

from agentic_primitives_gateway_client import AgenticPlatformClient, fetch_token_from_env

try:
    from rich.console import Console
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.table import Table
    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False
    Live = None
    console = None

# ── Configuration ─────────────────────────────────────────────────

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
REGION = "{region}"
TEAM_RUN_ID = uuid4().hex[:16]

client = AgenticPlatformClient(GATEWAY_URL, aws_from_environment=True)
{_TOKEN_REFRESH}
{shared_ns_config}{team_session_block}
# ── LLM loop ─────────────────────────────────────────────────────
{_LLM_LOOP}
# ── Task board UI ─────────────────────────────────────────────────


class TaskBoard:
    """Live-updating task board for terminal display."""

    def __init__(self):
        self.tasks: list[dict] = []
        self.status: dict[str, str] = {{}}  # task title -> status
        self.results: dict[str, str] = {{}}

    def set_tasks(self, tasks: list[dict]):
        self.tasks = tasks
        for t in tasks:
            self.status[t["title"]] = "pending"

    def update(self, title: str, status: str, result: str = ""):
        self.status[title] = status
        if result:
            self.results[title] = result[:80]

    def render(self):
        if not HAS_RICH:
            return self._render_plain()
        table = Table(title="Task Board", show_lines=True, expand=True)
        table.add_column("Status", width=12, justify="center")
        table.add_column("Worker", width=15)
        table.add_column("Task", ratio=2)
        table.add_column("Result", ratio=2)

        status_style = {{
            "pending": "[dim]pending[/dim]",
            "running": "[bold yellow]running[/bold yellow]",
            "done": "[bold green]done[/bold green]",
            "failed": "[bold red]failed[/bold red]",
        }}

        for t in self.tasks:
            title = t["title"]
            s = self.status.get(title, "pending")
            table.add_row(
                status_style.get(s, s),
                t.get("assigned_to", ""),
                title,
                self.results.get(title, ""),
            )
        return table

    def _render_plain(self) -> str:
        lines = ["  Task Board:"]
        for t in self.tasks:
            title = t["title"]
            s = self.status.get(title, "pending")
            worker = t.get("assigned_to", "")
            result = self.results.get(title, "")
            lines.append(f"    [{{s:8}}] {{worker:15}} {{title}}  {{result[:60]}}")
        return "\\n".join(lines)


board = TaskBoard()


# ── Planner ───────────────────────────────────────────────────────

PLANNER_MODEL = "{planner_model}"
PLANNER_PROMPT = """{planner_prompt}"""

WORKER_NAMES = {worker_names}


async def plan(message: str) -> list[dict]:
    """Run the planner to decompose a request into tasks."""
    worker_desc = ", ".join(WORKER_NAMES)
    prompt = (
        f"Decompose this request into tasks for these workers: {{worker_desc}}.\\n"
        f"Return a JSON array of tasks. Each task has: title, description, assigned_to.\\n"
        f"If a task depends on another task's output, include depends_on (list of task titles).\\n"
        f"Tasks without depends_on run in parallel. Tasks with depends_on wait for those to finish.\\n\\n"
        f"Request: {{message}}"
    )

    bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    messages = [{{"role": "user", "content": [{{"text": prompt}}]}}]
    response = bedrock.converse(
        modelId=PLANNER_MODEL,
        messages=messages,
        system=[{{"text": PLANNER_PROMPT}}],
        inferenceConfig={{"temperature": 0.7}},
    )
    text = "".join(
        b["text"] for b in response["output"]["message"]["content"] if "text" in b
    )

    try:
        start = text.index("[")
        end = text.rindex("]") + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return [{{"title": "Execute request", "description": message, "assigned_to": WORKER_NAMES[0]}}]


# ── Workers ───────────────────────────────────────────────────────

{workers_dict}

async def execute_task(task: dict, live=None, completed_results: dict | None = None) -> tuple[str, str]:
    """Execute a single task with the assigned worker. Returns (title, result).

    Each task gets its own browser/code_interpreter sessions for isolation.
    ``completed_results`` contains results from dependency tasks, injected
    as context into the task message.
    """
    title = task["title"]
    worker_name = task.get("assigned_to", WORKER_NAMES[0])
    worker = WORKERS.get(worker_name)
    if worker is None:
        board.update(title, "failed", f"Unknown worker: {{worker_name}}")
        if live:
            live.update(board.render())
        return title, f"Unknown worker: {{worker_name}}"

    board.update(title, "running")
    if live:
        live.update(board.render())

    namespace = f"team:{team_spec.name}:{{TEAM_RUN_ID}}"
    {shared_ns_line}

    # Per-task sessions for isolation during parallel execution
    task_sessions: dict[str, str] = {{}}

    def _on_tool(name, inp):
        short = json.dumps(inp)[:60]
        board.update(title, "running", f"[{{name}}] {{short}}")
        if live:
            live.update(board.render())
        elif HAS_RICH:
            console.print(f"  [dim]{{worker_name}}:[/dim] [cyan]{{name}}[/cyan]([dim]{{short}}[/dim])")
        else:
            print(f"  {{worker_name}}: {{name}}({{short}})")

    async def worker_execute(name: str, tool_input: dict, _sessions_ctx=task_sessions) -> str:
{worker_dispatch}        return f"Unknown tool: {{name}}"

    try:
        board.update(title, "running", "starting...")
        if live:
            live.update(board.render())

        # Build message with dependency context
        msg_parts = [f"Task: {{title}}\\n\\n{{task.get('description', '')}}"]
        if completed_results:
            deps = task.get("depends_on", [])
            for dep_title in deps:
                if dep_title in completed_results:
                    msg_parts.append(f"\\n\\nContext from '{{dep_title}}':\\n{{completed_results[dep_title]}}")
        message = "".join(msg_parts)

        result = await run_llm_loop(
            worker["model"], worker["system_prompt"], message,
            worker["tools"], worker["tool_config"],
            worker["max_turns"], worker["temperature"],
            worker_execute, REGION, on_tool=_on_tool,
        )
        summary = result.split("\\n")[0][:80] if result else "done"
        board.update(title, "done", summary)
    except Exception as e:
        result = f"Error: {{e}}"
        board.update(title, "failed", str(e)[:80])
    finally:
        await _cleanup_sessions(task_sessions)

    if live:
        live.update(board.render())
    return title, result


# ── Synthesizer ───────────────────────────────────────────────────

SYNTH_MODEL = "{synth_model}"
SYNTH_PROMPT = """{synth_prompt}"""


async def synthesize(message: str, results: list[tuple[str, str]]) -> str:
    """Synthesize worker results into a final response."""
    context = "\\n\\n".join(
        f"## {{title}}\\n{{result}}" for title, result in results
    )
    prompt = f"Original request: {{message}}\\n\\nTask results:\\n{{context}}"

    bedrock = boto3.client("bedrock-runtime", region_name=REGION)
    messages = [{{"role": "user", "content": [{{"text": prompt}}]}}]
    response = bedrock.converse(
        modelId=SYNTH_MODEL,
        messages=messages,
        system=[{{"text": SYNTH_PROMPT}}],
        inferenceConfig={{"temperature": 0.7}},
    )
    return "".join(
        b["text"] for b in response["output"]["message"]["content"] if "text" in b
    )


# ── Main ──────────────────────────────────────────────────────────


async def main():
    if HAS_RICH:
        console.print(Panel(
            f"[bold]{team_spec.name}[/bold]\\n"
            f"[dim]Workers:[/dim] {{', '.join(WORKER_NAMES)}}\\n"
            f"[dim]Gateway:[/dim] {{GATEWAY_URL}}\\n"
            f"[dim]Run:[/dim] {{TEAM_RUN_ID}}",
            title="Team", border_style="blue",
        ))
        console.print("[dim]Type 'quit' to exit.[/dim]\\n")
    else:
        print(f"Team: {team_spec.name} | Workers: {{', '.join(WORKER_NAMES)}}")
        print(f"Gateway: {{GATEWAY_URL}} | Run: {{TEAM_RUN_ID}}")
        print("\\nType 'quit' to exit.\\n")

    while True:
        try:
            user_input = (console.input("[bold green]You:[/bold green] ") if HAS_RICH else input("You: ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        # Plan
        print("\\nPlanning...")
        tasks = await plan(user_input)
        board.set_tasks(tasks)
        print(f"Created {{len(tasks)}} tasks\\n")

        # Execute in dependency waves — tasks without deps run first,
        # then tasks whose deps are done, etc.
        completed: dict[str, str] = {{}}  # title -> result

        async def _run_wave(wave_tasks, live=None):
            coros = [execute_task(t, live, completed) for t in wave_tasks]
            wave_results = await asyncio.gather(*coros)
            for t, r in wave_results:
                completed[t] = r

        if HAS_RICH:
            with Live(board.render(), console=console, refresh_per_second=4) as live:
                remaining = list(tasks)
                while remaining:
                    # Find tasks whose dependencies are all satisfied
                    ready = [t for t in remaining
                             if all(d in completed for d in t.get("depends_on", []))]
                    if not ready:
                        # Deadlock or missing deps — run everything remaining
                        ready = remaining
                    remaining = [t for t in remaining if t not in ready]
                    await _run_wave(ready, live)
                    live.update(board.render())
            console.print()
        else:
            remaining = list(tasks)
            while remaining:
                ready = [t for t in remaining
                         if all(d in completed for d in t.get("depends_on", []))]
                if not ready:
                    ready = remaining
                remaining = [t for t in remaining if t not in ready]
                await _run_wave(ready)
                print(board._render_plain())
            print()

        # Synthesize
        print("Synthesizing...")
        response = await synthesize(user_input, list(completed.items()))
        if HAS_RICH:
            console.print(Panel(Markdown(response), title="Response", border_style="green"))
        else:
            print(f"\\nResponse:\\n{{response}}")
        print()
{team_cleanup_call}

if __name__ == "__main__":
    asyncio.run(main())
'''


def _build_workers_dict(worker_configs: list[dict]) -> str:
    """Generate the WORKERS dict with per-worker config."""
    # Build as a Python dict literal, then serialize
    workers: dict[str, Any] = {}
    for wc in worker_configs:
        tools_list = json.loads(wc["tools_json"])
        tool_config = {
            "tools": [
                {
                    "toolSpec": {
                        "name": t["name"],
                        "description": t["description"],
                        "inputSchema": {"json": t["input_schema"]},
                    }
                }
                for t in tools_list
            ]
        }
        workers[wc["name"]] = {
            "model": wc["model"],
            "system_prompt": wc["system_prompt"],
            "max_turns": wc["max_turns"],
            "temperature": wc["temperature"],
            "tools": tools_list,
            "tool_config": tool_config,
        }

    return f"WORKERS = {json.dumps(workers, indent=2)}"


def _all_worker_prims(worker_configs: list[dict]) -> list[str]:
    """Union of all primitives across all workers."""
    all_prims: set[str] = set()
    for wc in worker_configs:
        all_prims.update(wc["prims"])
    return sorted(all_prims)


def _indent(text: str, spaces: int) -> str:
    """Indent each line of text by the given number of spaces."""
    prefix = " " * spaces
    return "\n".join(prefix + line if line.strip() else line for line in text.split("\n"))
