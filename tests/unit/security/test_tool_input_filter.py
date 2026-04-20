"""Vuln 1: LLM cannot override bound tool-context kwargs via tool_input."""

from __future__ import annotations

import pytest

from agentic_primitives_gateway.agents.tools.catalog import (
    ToolDefinition,
    _filter_to_schema,
    execute_tool,
)


def _schema(*props: str) -> dict:
    return {
        "type": "object",
        "properties": {p: {"type": "string"} for p in props},
        "required": list(props),
    }


class TestFilterToSchema:
    def test_drops_keys_not_in_schema(self):
        safe = _filter_to_schema(
            {"key": "k", "content": "c", "namespace": "other-user"},
            _schema("key", "content"),
        )
        assert safe == {"key": "k", "content": "c"}
        assert "namespace" not in safe

    def test_empty_schema_rejects_all_input(self):
        """Fail-closed: a tool with no declared properties accepts no input."""
        safe = _filter_to_schema(
            {"anything": "x"},
            {"type": "object", "properties": {}},
        )
        assert safe == {}

    def test_missing_properties_key_rejects_all_input(self):
        safe = _filter_to_schema({"x": "y"}, {"type": "object"})
        assert safe == {}

    def test_non_dict_schema_rejects_all_input(self):
        safe = _filter_to_schema({"x": "y"}, "not a dict")  # type: ignore[arg-type]
        assert safe == {}


@pytest.mark.asyncio
async def test_execute_tool_filters_llm_override_of_namespace():
    """Regression: an LLM supplying ``namespace`` cannot override the bound value."""
    from functools import partial

    called_with: dict[str, object] = {}

    async def handler(namespace: str, key: str, content: str) -> str:
        called_with.update(namespace=namespace, key=key, content=content)
        return "ok"

    # Simulate the binding pattern used by ``_bind_handler``.
    bound = partial(handler, namespace="trusted-ns")
    tool = ToolDefinition(
        name="remember",
        description="test",
        primitive="memory",
        input_schema=_schema("key", "content"),
        handler=bound,
    )

    # LLM attempts to override the bound namespace.
    result = await execute_tool(
        "remember",
        {"key": "k", "content": "c", "namespace": "attacker-ns"},
        [tool],
    )
    assert result == "ok"
    # Bound value wins; the LLM's injection was stripped.
    assert called_with["namespace"] == "trusted-ns"


@pytest.mark.asyncio
async def test_execute_tool_filters_llm_override_of_session_id():
    from functools import partial

    called_with: dict[str, object] = {}

    async def handler(session_id: str, url: str) -> str:
        called_with.update(session_id=session_id, url=url)
        return "ok"

    bound = partial(handler, session_id="my-session")
    tool = ToolDefinition(
        name="navigate",
        description="test",
        primitive="browser",
        input_schema=_schema("url"),
        handler=bound,
    )

    await execute_tool(
        "navigate",
        {"url": "https://example.com", "session_id": "victims-session"},
        [tool],
    )
    assert called_with["session_id"] == "my-session"
    assert called_with["url"] == "https://example.com"
