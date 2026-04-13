"""Build callable tools from the gateway's tool catalog.

Fetches tool definitions (name, description, input_schema) from the gateway
and returns plain Python functions that call the corresponding API endpoints.
The functions have proper ``__name__``, ``__doc__``, and type annotations set
from the catalog, so any agent framework can use them directly.

Usage (async)::

    tools = await platform.get_tools(
        ["memory", "browser", "code_interpreter"],
        namespace="agent:my-agent",
    )
    # tools is a list of async callables — pass to any framework
    agent = create_agent(model, tools=tools)

Usage (sync)::

    tools = platform.get_tools_sync(
        ["memory", "browser", "code_interpreter"],
        namespace="agent:my-agent",
    )
    # tools is a list of sync callables — pass to any framework
    agent = Agent(model=model, tools=tools)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentic_primitives_gateway_client.client import AgenticPlatformClient
    from agentic_primitives_gateway_client.primitives.browser import Browser
    from agentic_primitives_gateway_client.primitives.code_interpreter import CodeInterpreter
    from agentic_primitives_gateway_client.primitives.identity import Identity
    from agentic_primitives_gateway_client.primitives.memory import Memory
    from agentic_primitives_gateway_client.primitives.tools import Tools

logger = logging.getLogger(__name__)

# JSON Schema type → Python type annotation
_SCHEMA_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}


def _make_annotations(input_schema: dict[str, Any]) -> dict[str, Any]:
    """Build __annotations__ dict from a JSON Schema."""
    props = input_schema.get("properties", {})
    annotations: dict[str, Any] = {}
    for name, prop in props.items():
        annotations[name] = _SCHEMA_TYPE_MAP.get(prop.get("type", "string"), str)
    annotations["return"] = str
    return annotations


def _build_tool_spec(name: str, description: str, input_schema: dict[str, Any]) -> dict[str, Any]:
    """Build a Bedrock-compatible tool spec from catalog data.

    This format is understood by Strands, Bedrock converse, and other
    frameworks that use the Bedrock tool calling convention.
    """
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    spec_props: dict[str, Any] = {}
    for prop_name, prop_def in properties.items():
        spec_props[prop_name] = {
            "description": prop_def.get("description", f"Parameter {prop_name}"),
            "type": prop_def.get("type", "string"),
        }
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": spec_props,
                "required": required,
            }
        },
    }


def _set_tool_metadata(
    fn: Callable,
    name: str,
    description: str,
    input_schema: dict[str, Any],
) -> Callable:
    """Set tool metadata on a callable for framework compatibility.

    Sets standard Python metadata (__name__, __doc__, __annotations__)
    plus ``tool_spec`` and ``tool_name`` for Bedrock-compatible frameworks
    (Strands, etc.).
    """
    fn.__name__ = name
    fn.__qualname__ = name
    fn.__doc__ = description
    fn.__annotations__ = _make_annotations(input_schema)
    # Bedrock-compatible tool spec (used by Strands and other frameworks)
    fn.tool_spec = _build_tool_spec(name, description, input_schema)  # type: ignore[attr-defined]
    fn.tool_name = name  # type: ignore[attr-defined]
    fn.tool_type = "function"  # type: ignore[attr-defined]
    return fn


# ── Async tool builders per primitive ─────────────────────────────────


def _memory_tool_async(
    tool_name: str,
    memory: Memory,
) -> Callable[..., Any] | None:
    """Return an async callable for a memory tool."""
    if tool_name == "remember":

        async def _remember(key: str, content: str, source: str = "") -> str:
            return await memory.remember(key, content, source)

        return _remember
    if tool_name == "recall":

        async def _recall(key: str) -> str:
            return await memory.recall(key)

        return _recall
    if tool_name == "search_memory":

        async def _search(query: str, top_k: int = 5) -> str:
            return await memory.search(query, top_k)

        return _search
    if tool_name == "list_memories":

        async def _list(limit: int = 20) -> str:
            return await memory.list(limit)

        return _list
    if tool_name == "forget":

        async def _forget(key: str) -> str:
            return await memory.forget(key)

        return _forget
    return None


def _browser_tool_async(
    tool_name: str,
    browser: Browser,
) -> Callable[..., Any] | None:
    """Return an async callable for a browser tool.

    Auto-starts a browser session on first use if none is active.
    """

    async def _ensure_session() -> None:
        if not browser.session_id:
            await browser.start()

    if tool_name == "navigate":

        async def _navigate(url: str) -> str:
            await _ensure_session()
            return await browser.navigate(url)

        return _navigate
    if tool_name == "read_page":

        async def _read_page() -> str:
            await _ensure_session()
            return await browser.get_page_content()

        return _read_page
    if tool_name == "click":

        async def _click(selector: str) -> str:
            await _ensure_session()
            return await browser.click(selector)

        return _click
    if tool_name == "type_text":

        async def _type_text(selector: str, text: str) -> str:
            await _ensure_session()
            return await browser.type_text(selector, text)

        return _type_text
    if tool_name == "screenshot":

        async def _screenshot() -> str:
            await _ensure_session()
            return await browser.screenshot()

        return _screenshot
    if tool_name == "evaluate_js":

        async def _evaluate_js(expression: str) -> str:
            await _ensure_session()
            return await browser.evaluate(expression)

        return _evaluate_js
    return None


def _code_tool_async(
    tool_name: str,
    code: CodeInterpreter,
) -> Callable[..., Any] | None:
    """Return an async callable for a code_interpreter tool."""
    if tool_name == "execute_code":

        async def _execute(code_str: str, language: str = "python") -> str:
            return await code.execute(code_str, language)

        return _execute
    return None


def _identity_tool_async(
    tool_name: str,
    identity: Identity,
) -> Callable[..., Any] | None:
    """Return an async callable for an identity tool."""
    if tool_name == "get_token":

        async def _get_token(credential_provider: str, scopes: str = "") -> str:
            scope_list = [s.strip() for s in scopes.split(",") if s.strip()] if scopes else None
            wt = await identity.get_workload_token("default")
            return await identity.get_token(credential_provider, wt, scopes=scope_list)

        return _get_token

    if tool_name == "get_api_key":

        async def _get_api_key(credential_provider: str) -> str:
            wt = await identity.get_workload_token("default")
            return await identity.get_api_key(credential_provider, wt)

        return _get_api_key

    return None


def _tools_tool_async(
    tool_name: str,
    tools_client: Tools,
) -> Callable[..., Any] | None:
    """Return an async callable for a tools (MCP) tool."""
    if tool_name == "search_tools":

        async def _search(query: str, max_results: int = 10) -> str:
            result = await tools_client.search(query, max_results)
            return json.dumps(result, default=str)

        return _search

    if tool_name == "invoke_tool":

        async def _invoke(tool_name: str, params: str = "{}") -> str:
            try:
                parsed = json.loads(params) if isinstance(params, str) else params
                result = await tools_client.invoke(tool_name, parsed)
                return json.dumps(result, default=str) if not isinstance(result, str) else result
            except Exception as e:
                return f"Error invoking tool '{tool_name}': {e}"

        return _invoke

    return None


# ── Sync tool builders per primitive ──────────────────────────────────


class _SyncMemoryClient:
    """Sync HTTP client for memory operations.

    The async ``AgenticPlatformClient`` uses httpx.AsyncClient which is
    bound to an event loop. Calling it from sync code via
    ``run_until_complete`` causes "Event loop is closed" errors because
    httpx's connection pool cleanup conflicts with loop lifecycle.

    This class uses a plain ``httpx.Client`` (sync) to avoid the issue.
    """

    def __init__(self, client: AgenticPlatformClient, namespace: str) -> None:
        import httpx as _httpx

        self._namespace = namespace
        self._base_url = str(client._client.base_url)
        self._headers = dict(client._headers)
        self._http = _httpx.Client(base_url=self._base_url, timeout=30)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._http.request(method, path, headers=self._headers, **kwargs)
        if resp.status_code >= 400:
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            raise RuntimeError(f"HTTP {resp.status_code}: {body.get('detail', resp.text[:200])}")
        return resp.json()

    def store(self, key: str, content: str, source: str = "") -> str:
        ns = self._namespace
        metadata = {"source": source} if source else {}
        self._request("POST", f"/api/v1/memory/{ns}", json={"key": key, "content": content, "metadata": metadata})
        return f"Stored memory '{key}'"

    def retrieve(self, key: str) -> str:
        ns = self._namespace
        try:
            r = self._request("GET", f"/api/v1/memory/{ns}/{key}")
            return r.get("content", "Not found")
        except RuntimeError:
            return f"No memory found for key '{key}'"

    def search(self, query: str, top_k: int = 5) -> str:
        ns = self._namespace
        r = self._request("POST", f"/api/v1/memory/{ns}/search", json={"query": query, "top_k": top_k})
        results = r.get("results", [])
        if not results:
            return "No relevant memories found."
        lines = [f"Found {len(results)} memories:"]
        for item in results:
            rec = item.get("record", {})
            score = item.get("score", 0)
            lines.append(f"  [{rec.get('key', '?')}] (score: {score:.2f}) {rec.get('content', '')}")
        return "\n".join(lines)

    def list(self, limit: int = 20) -> str:
        ns = self._namespace
        r = self._request("GET", f"/api/v1/memory/{ns}", params={"limit": limit})
        records = r.get("records", [])
        if not records:
            return "No memories stored."
        lines = [f"{len(records)} memories:"]
        for rec in records:
            lines.append(f"  [{rec.get('key', '?')}] {rec.get('content', '')}")
        return "\n".join(lines)

    def forget(self, key: str) -> str:
        ns = self._namespace
        self._request("DELETE", f"/api/v1/memory/{ns}/{key}")
        return f"Deleted memory '{key}'"


def _memory_tool_sync(
    tool_name: str,
    memory: Memory,
) -> Callable[..., Any] | None:
    """Return a sync callable for a memory tool.

    Uses ``_SyncMemoryClient`` with a sync httpx client to avoid
    event loop conflicts from the async ``AgenticPlatformClient``.
    """
    sync = _SyncMemoryClient(memory._client, memory.namespace)

    if tool_name == "remember":

        def _remember(key: str, content: str, source: str = "") -> str:
            return sync.store(key, content, source)

        return _remember
    if tool_name == "recall":

        def _recall(key: str) -> str:
            return sync.retrieve(key)

        return _recall
    if tool_name == "search_memory":

        def _search(query: str, top_k: int = 5) -> str:
            return sync.search(query, top_k)

        return _search
    if tool_name == "list_memories":

        def _list(limit: int = 20) -> str:
            return sync.list(limit)

        return _list
    if tool_name == "forget":

        def _forget(key: str) -> str:
            return sync.forget(key)

        return _forget
    return None


def _browser_tool_sync(
    tool_name: str,
    browser: Browser,
) -> Callable[..., Any] | None:
    """Return a sync callable for a browser tool.

    Auto-starts a browser session on first use if none is active.
    """

    def _ensure_session() -> None:
        if not browser.session_id:
            browser.start_sync()

    if tool_name == "navigate":

        def _navigate(url: str) -> str:
            _ensure_session()
            return browser.navigate_sync(url)

        return _navigate
    if tool_name == "read_page":

        def _read_page() -> str:
            _ensure_session()
            return browser.get_page_content_sync()

        return _read_page
    if tool_name == "click":

        def _click(selector: str) -> str:
            _ensure_session()
            return browser.click_sync(selector)

        return _click
    if tool_name == "type_text":

        def _type_text(selector: str, text: str) -> str:
            _ensure_session()
            return browser.type_text_sync(selector, text)

        return _type_text
    if tool_name == "screenshot":

        def _screenshot() -> str:
            _ensure_session()
            return browser.screenshot_sync()

        return _screenshot
    if tool_name == "evaluate_js":

        def _evaluate_js(expression: str) -> str:
            _ensure_session()
            return browser.evaluate_sync(expression)

        return _evaluate_js
    return None


def _code_tool_sync(
    tool_name: str,
    code: CodeInterpreter,
) -> Callable[..., Any] | None:
    """Return a sync callable for a code_interpreter tool."""
    if tool_name == "execute_code":

        def _execute(code_str: str, language: str = "python") -> str:
            return code.execute_sync(code_str, language)

        return _execute
    return None


def _identity_tool_sync(
    tool_name: str,
    identity: Identity,
) -> Callable[..., Any] | None:
    """Return a sync callable for an identity tool."""
    if tool_name == "get_token":

        def _get_token(credential_provider: str, scopes: str = "") -> str:
            scope_list = [s.strip() for s in scopes.split(",") if s.strip()] if scopes else None
            wt = identity.get_workload_token_sync("default")
            return identity.get_token_sync(credential_provider, wt, scopes=scope_list)

        return _get_token

    if tool_name == "get_api_key":

        def _get_api_key(credential_provider: str) -> str:
            wt = identity.get_workload_token_sync("default")
            return identity.get_api_key_sync(credential_provider, wt)

        return _get_api_key

    return None


def _tools_tool_sync(
    tool_name: str,
    tools_client: Tools,
) -> Callable[..., Any] | None:
    """Return a sync callable for a tools (MCP) tool."""
    if tool_name == "search_tools":

        def _search(query: str, max_results: int = 10) -> str:
            result = tools_client.search_sync(query, max_results)
            return json.dumps(result, default=str)

        return _search

    if tool_name == "invoke_tool":

        def _invoke(tool_name: str, params: str = "{}") -> str:
            try:
                parsed = json.loads(params) if isinstance(params, str) else params
                result = tools_client.invoke_sync(tool_name, parsed)
                return json.dumps(result, default=str) if not isinstance(result, str) else result
            except Exception as e:
                return f"Error invoking tool '{tool_name}': {e}"

        return _invoke

    return None


# ── Primitive helper constructors ─────────────────────────────────────

_ASYNC_BUILDERS: dict[str, Callable] = {
    "memory": _memory_tool_async,
    "browser": _browser_tool_async,
    "code_interpreter": _code_tool_async,
    "identity": _identity_tool_async,
    "tools": _tools_tool_async,
}

_SYNC_BUILDERS: dict[str, Callable] = {
    "memory": _memory_tool_sync,
    "browser": _browser_tool_sync,
    "code_interpreter": _code_tool_sync,
    "identity": _identity_tool_sync,
    "tools": _tools_tool_sync,
}


# ── Public API ────────────────────────────────────────────────────────


async def build_tools_async(
    client: AgenticPlatformClient,
    primitives: list[str],
    *,
    namespace: str = "",
    session_id: str | None = None,
    observability: Any | None = None,
) -> list[Callable]:
    """Fetch the tool catalog and return async callables for the requested primitives.

    Each returned function has ``__name__``, ``__doc__``, and ``__annotations__``
    set from the gateway's tool catalog. Pass them directly to any agent framework.

    Args:
        client: The platform client instance.
        primitives: List of primitive names to include (e.g. ``["memory", "browser"]``).
        namespace: Memory namespace (required if "memory" is in primitives).
        session_id: Session ID for browser/code_interpreter state tracking.
        observability: Optional Observability instance for auto-tracing.

    Returns:
        List of async callables ready to use as agent tools.
    """
    from agentic_primitives_gateway_client.primitives.browser import Browser
    from agentic_primitives_gateway_client.primitives.code_interpreter import CodeInterpreter
    from agentic_primitives_gateway_client.primitives.identity import Identity
    from agentic_primitives_gateway_client.primitives.memory import Memory
    from agentic_primitives_gateway_client.primitives.tools import Tools

    # Fetch the tool catalog from the gateway
    catalog = await client.get_tool_catalog()

    # Create primitive helpers as needed
    helpers: dict[str, Any] = {}
    if "memory" in primitives:
        helpers["memory"] = Memory(client, namespace=namespace, session_id=session_id, observability=observability)
    if "browser" in primitives:
        helpers["browser"] = Browser(client)
    if "code_interpreter" in primitives:
        helpers["code_interpreter"] = CodeInterpreter(client)
    if "identity" in primitives:
        helpers["identity"] = Identity(client)
    if "tools" in primitives:
        helpers["tools"] = Tools(client)

    # Track helpers on the client for session cleanup in client.close()
    if hasattr(client, "_tool_helpers"):
        for h in helpers.values():
            if hasattr(h, "session_id"):
                client._tool_helpers.append(h)

    # Build tool functions
    tools: list[Callable] = []
    for prim_name in primitives:
        builder = _ASYNC_BUILDERS.get(prim_name)
        helper = helpers.get(prim_name)
        if builder is None or helper is None:
            logger.warning("No async tool builder for primitive %r, skipping", prim_name)
            continue

        for tool_def in catalog.get(prim_name, []):
            fn = builder(tool_def["name"], helper)
            if fn is None:
                logger.debug("No handler for tool %s:%s, skipping", prim_name, tool_def["name"])
                continue
            _set_tool_metadata(fn, tool_def["name"], tool_def["description"], tool_def["input_schema"])
            tools.append(fn)

    return tools


def build_tools_sync(
    client: AgenticPlatformClient,
    primitives: list[str],
    *,
    namespace: str = "",
    session_id: str | None = None,
    observability: Any | None = None,
) -> list[Callable]:
    """Fetch the tool catalog and return sync callables for the requested primitives.

    Same as :func:`build_tools_async` but returns synchronous functions.
    Use this for frameworks that expect sync callables (e.g., Strands).

    Args:
        client: The platform client instance.
        primitives: List of primitive names to include.
        namespace: Memory namespace.
        session_id: Session ID for browser/code_interpreter.
        observability: Optional Observability instance.

    Returns:
        List of sync callables ready to use as agent tools.
    """
    from agentic_primitives_gateway_client.primitives.browser import Browser
    from agentic_primitives_gateway_client.primitives.code_interpreter import CodeInterpreter
    from agentic_primitives_gateway_client.primitives.identity import Identity
    from agentic_primitives_gateway_client.primitives.memory import Memory
    from agentic_primitives_gateway_client.primitives.tools import Tools

    # Fetch the tool catalog synchronously
    loop = asyncio.new_event_loop()
    try:
        catalog = loop.run_until_complete(client.get_tool_catalog())
    finally:
        loop.close()

    # Create primitive helpers as needed
    helpers: dict[str, Any] = {}
    if "memory" in primitives:
        helpers["memory"] = Memory(client, namespace=namespace, session_id=session_id, observability=observability)
    if "browser" in primitives:
        helpers["browser"] = Browser(client)
    if "code_interpreter" in primitives:
        helpers["code_interpreter"] = CodeInterpreter(client)
    if "identity" in primitives:
        helpers["identity"] = Identity(client)
    if "tools" in primitives:
        helpers["tools"] = Tools(client)

    # Track helpers on the client for session cleanup in client.close_sync()
    if hasattr(client, "_tool_helpers"):
        for h in helpers.values():
            if hasattr(h, "session_id"):
                client._tool_helpers.append(h)

    # Build tool functions
    tools: list[Callable] = []
    for prim_name in primitives:
        builder = _SYNC_BUILDERS.get(prim_name)
        helper = helpers.get(prim_name)
        if builder is None or helper is None:
            logger.warning("No sync tool builder for primitive %r, skipping", prim_name)
            continue

        for tool_def in catalog.get(prim_name, []):
            fn = builder(tool_def["name"], helper)
            if fn is None:
                logger.debug("No handler for tool %s:%s, skipping", prim_name, tool_def["name"])
                continue
            _set_tool_metadata(fn, tool_def["name"], tool_def["description"], tool_def["input_schema"])
            tools.append(fn)

    return tools
