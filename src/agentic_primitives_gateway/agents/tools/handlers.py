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
