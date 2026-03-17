"""Export declarative agent/team specs as standalone Python code.

Generates a Python script that uses ``agentic-primitives-gateway-client``
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

# ── Tool schema extraction ───────────────────────────────────────────


def _tools_for_primitives(primitives: dict[str, PrimitiveConfig]) -> list[dict[str, Any]]:
    """Build Bedrock-compatible tool definitions from enabled primitives."""
    tools: list[dict[str, Any]] = []
    for prim_name, prim_cfg in primitives.items():
        if not prim_cfg.enabled or prim_name in ("agents",):
            continue
        catalog_key = prim_name
        if catalog_key not in _TOOL_CATALOG:
            continue
        allowed = set(prim_cfg.tools) if prim_cfg.tools else None
        for td in _TOOL_CATALOG[catalog_key]:
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


# ── Primitives list ──────────────────────────────────────────────────


def _enabled_primitives(primitives: dict[str, PrimitiveConfig]) -> list[str]:
    return [k for k, v in primitives.items() if v.enabled and k != "agents"]


# ── Code generation ──────────────────────────────────────────────────


def export_agent(spec: AgentSpec) -> str:
    """Generate a standalone Python script from an agent spec."""
    prims = _enabled_primitives(spec.primitives)
    tools = _tools_for_primitives(spec.primitives)
    tools_json = json.dumps(tools, indent=2)
    dispatch_cases = _build_dispatch_cases(prims)
    system_prompt = spec.system_prompt.replace('"""', '\\"\\"\\"')
    region = _extract_region(spec.model)

    return f'''#!/usr/bin/env python3
"""Exported agent: {spec.name}

Generated from declarative agent spec. Uses the gateway client for
primitive calls and boto3 Bedrock converse() for the LLM loop.

Edit the loop, add custom tools, change branching logic — the
gateway still handles provider abstraction and credentials.

Usage:
    pip install agentic-primitives-gateway-client[aws]
    export GATEWAY_URL=http://localhost:8000
    python {spec.name}.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from uuid import uuid4

import boto3

from agentic_primitives_gateway_client import AgenticPlatformClient

# ── Configuration ─────────────────────────────────────────────────

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
MODEL_ID = "{spec.model}"
MAX_TURNS = {spec.max_turns}
TEMPERATURE = {spec.temperature}
NAMESPACE = "agent:{spec.name}"
SESSION_ID = f"session-{{uuid4().hex[:8]}}"

SYSTEM_PROMPT = """{system_prompt}"""

# ── Gateway client ────────────────────────────────────────────────

client = AgenticPlatformClient(GATEWAY_URL, aws_from_environment=True)

# Optional: set auth token for multi-user environments
# client.set_auth_token(os.environ.get("AUTH_TOKEN", ""))

# ── Tool definitions (Bedrock converse format) ────────────────────

TOOLS = {tools_json}

TOOL_CONFIG = {{
    "tools": [{{"toolSpec": {{
        "name": t["name"],
        "description": t["description"],
        "inputSchema": {{"json": t["input_schema"]}},
    }}}} for t in TOOLS]
}}

# ── Tool dispatch ─────────────────────────────────────────────────


async def execute_tool(name: str, tool_input: dict) -> str:
    """Route a tool call to the appropriate gateway primitive."""
{dispatch_cases}    return f"Unknown tool: {{name}}"


# ── LLM loop ─────────────────────────────────────────────────────


async def run(message: str) -> str:
    """Run the agent loop for a single user message."""
    bedrock = boto3.client("bedrock-runtime", region_name="{region}")

    messages = [{{"role": "user", "content": [{{"text": message}}]}}]

    for turn in range(MAX_TURNS):
        kwargs = {{
            "modelId": MODEL_ID,
            "messages": messages,
            "system": [{{"text": SYSTEM_PROMPT}}],
            "inferenceConfig": {{"temperature": TEMPERATURE}},
        }}
        if TOOLS:
            kwargs["toolConfig"] = TOOL_CONFIG

        response = bedrock.converse(**kwargs)
        output = response["output"]["message"]
        messages.append(output)
        stop_reason = response["stopReason"]

        if stop_reason != "tool_use":
            # Extract final text
            return "".join(
                block["text"] for block in output["content"] if "text" in block
            )

        # Execute tool calls
        tool_results = []
        for block in output["content"]:
            if "toolUse" not in block:
                continue
            tool_use = block["toolUse"]
            tool_name = tool_use["name"]
            tool_input = tool_use["input"]

            print(f"  [tool] {{tool_name}}({{json.dumps(tool_input)[:100]}})")
            result = await execute_tool(tool_name, tool_input)

            tool_results.append({{
                "toolResult": {{
                    "toolUseId": tool_use["toolUseId"],
                    "content": [{{"text": result}}],
                }}
            }})

        messages.append({{"role": "user", "content": tool_results}})

    return "(max turns reached)"


# ── Main ──────────────────────────────────────────────────────────


async def main():
    print(f"Agent: {spec.name}")
    print(f"Model: {{MODEL_ID}}")
    print(f"Gateway: {{GATEWAY_URL}}")
    print(f"Session: {{SESSION_ID}}")
    print()
    print("Type 'quit' to exit.\\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        print()
        response = await run(user_input)
        print(f"Assistant: {{response}}\\n")


if __name__ == "__main__":
    asyncio.run(main())
'''


def _extract_region(model_id: str) -> str:
    """Extract AWS region hint from model ID (e.g. 'us.' prefix -> us-east-1)."""
    if model_id.startswith("us."):
        return "us-east-1"
    if model_id.startswith("eu."):
        return "eu-west-1"
    if model_id.startswith("ap."):
        return "ap-northeast-1"
    return "us-east-1"


def _build_dispatch_cases(prims: list[str]) -> str:
    """Generate the tool dispatch if/elif chain."""
    lines: list[str] = []

    if "memory" in prims:
        lines.extend(
            [
                '    if name == "remember":',
                '        r = await client.store_memory(NAMESPACE, tool_input["key"], tool_input["content"])',
                "        return f\"Stored: {tool_input['key']}\"",
                '    elif name == "recall":',
                '        r = await client.retrieve_memory(NAMESPACE, tool_input["key"])',
                '        return r.get("content", "Not found") if r else "Not found"',
                '    elif name == "search_memory":',
                '        results = await client.search_memory(NAMESPACE, tool_input["query"], top_k=tool_input.get("top_k", 5))',
                '        return json.dumps(results.get("results", []), indent=2)',
                '    elif name == "forget":',
                '        await client.delete_memory(NAMESPACE, tool_input["key"])',
                "        return f\"Deleted: {tool_input['key']}\"",
                '    elif name == "list_memories":',
                '        r = await client.list_memories(NAMESPACE, limit=tool_input.get("limit", 20))',
                '        return json.dumps(r.get("records", []), indent=2)',
            ]
        )
    if "browser" in prims:
        prefix = "elif" if lines else "if"
        lines.extend(
            [
                f'    {prefix} name == "navigate":',
                '        r = await client.browser_navigate(tool_input["url"])',
                "        return json.dumps(r)",
                '    elif name == "read_page":',
                "        r = await client.browser_read_page()",
                '        return r.get("content", "")',
                '    elif name == "click":',
                '        r = await client.browser_click(tool_input["selector"])',
                "        return json.dumps(r)",
                '    elif name == "type_text":',
                '        r = await client.browser_type(tool_input["selector"], tool_input["text"])',
                "        return json.dumps(r)",
                '    elif name == "screenshot":',
                "        r = await client.browser_screenshot()",
                '        return "Screenshot taken"',
                '    elif name == "evaluate_js":',
                '        r = await client.browser_evaluate_js(tool_input["expression"])',
                "        return json.dumps(r)",
            ]
        )
    if "code_interpreter" in prims:
        prefix = "elif" if lines else "if"
        lines.extend(
            [
                f'    {prefix} name == "execute_code":',
                '        r = await client.execute_code(tool_input["code"], language=tool_input.get("language", "python"))',
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

    if not lines:
        return "    pass  # No primitives enabled\n"

    return "\n".join(lines) + "\n"
