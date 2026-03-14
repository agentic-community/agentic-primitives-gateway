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
import functools
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


def _set_tool_metadata(
    fn: Callable,
    name: str,
    description: str,
    input_schema: dict[str, Any],
) -> Callable:
    """Wrap a callable and set __name__, __doc__, and __annotations__.

    Always wraps in a plain function so metadata can be set even on
    bound methods (which don't allow __name__ assignment).
    """
    inner = fn

    if asyncio.iscoroutinefunction(inner):

        @functools.wraps(inner)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            return await inner(*args, **kwargs)

        wrapped: Any = async_wrapper
    else:

        @functools.wraps(inner)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            return inner(*args, **kwargs)

        wrapped = sync_wrapper

    wrapped.__name__ = name
    wrapped.__qualname__ = name
    wrapped.__doc__ = description
    wrapped.__annotations__ = _make_annotations(input_schema)
    result: Callable = wrapped
    return result


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
    """Return an async callable for a browser tool."""
    if tool_name == "navigate":

        async def _navigate(url: str) -> str:
            return await browser.navigate(url)

        return _navigate
    if tool_name == "read_page":

        async def _read_page() -> str:
            return await browser.get_page_content()

        return _read_page
    if tool_name == "click":

        async def _click(selector: str) -> str:
            return await browser.click(selector)

        return _click
    if tool_name == "type_text":

        async def _type_text(selector: str, text: str) -> str:
            return await browser.type_text(selector, text)

        return _type_text
    if tool_name == "screenshot":

        async def _screenshot() -> str:
            return await browser.screenshot()

        return _screenshot
    if tool_name == "evaluate_js":

        async def _evaluate_js(expression: str) -> str:
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
            parsed = json.loads(params) if isinstance(params, str) else params
            result = await tools_client.invoke(tool_name, parsed)
            return json.dumps(result, default=str) if not isinstance(result, str) else result

        return _invoke

    return None


# ── Sync tool builders per primitive ──────────────────────────────────


def _memory_tool_sync(
    tool_name: str,
    memory: Memory,
) -> Callable[..., Any] | None:
    """Return a sync callable for a memory tool."""
    if tool_name == "remember":

        def _remember(key: str, content: str, source: str = "") -> str:
            return memory.remember_sync(key, content, source)

        return _remember
    if tool_name == "recall":

        def _recall(key: str) -> str:
            return memory.recall_sync(key)

        return _recall
    if tool_name == "search_memory":

        def _search(query: str, top_k: int = 5) -> str:
            return memory.search_sync(query, top_k)

        return _search
    if tool_name == "list_memories":

        def _list(limit: int = 20) -> str:
            return memory.list_sync(limit)

        return _list
    if tool_name == "forget":

        def _forget(key: str) -> str:
            return memory.forget_sync(key)

        return _forget
    return None


def _browser_tool_sync(
    tool_name: str,
    browser: Browser,
) -> Callable[..., Any] | None:
    """Return a sync callable for a browser tool."""
    if tool_name == "navigate":

        def _navigate(url: str) -> str:
            return browser.navigate_sync(url)

        return _navigate
    if tool_name == "read_page":

        def _read_page() -> str:
            return browser.get_page_content_sync()

        return _read_page
    if tool_name == "click":

        def _click(selector: str) -> str:
            return browser.click_sync(selector)

        return _click
    if tool_name == "type_text":

        def _type_text(selector: str, text: str) -> str:
            return browser.type_text_sync(selector, text)

        return _type_text
    if tool_name == "screenshot":

        def _screenshot() -> str:
            return browser.screenshot_sync()

        return _screenshot
    if tool_name == "evaluate_js":

        def _evaluate_js(expression: str) -> str:
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
            parsed = json.loads(params) if isinstance(params, str) else params
            result = tools_client.invoke_sync(tool_name, parsed)
            return json.dumps(result, default=str) if not isinstance(result, str) else result

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
