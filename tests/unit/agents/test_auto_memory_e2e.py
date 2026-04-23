"""Intent-level test: auto_memory hook persists conversation and re-hydrates it next session.

Contract: when an agent has ``hooks.auto_memory=true``, the runner
writes each turn to the memory primitive's event store via
``create_event``, and on the next run with the same ``session_id``
the prior turns are loaded via ``get_last_turns`` and injected into
the LLM's message history.

Existing ``test_auto_memory_stores_turn`` (``test_runner.py:156-170``)
only asserts that ``create_event`` was awaited — it mocks the memory
provider with an ``AsyncMock`` so nothing actually lands in a store
and nothing is read back on a second run.  If the code path that
loads history silently broke (e.g. ``get_last_turns`` was called but
its result never flowed into ``ctx.messages``), the existing test
would still pass.  Observable impact of that regression: agents
forget everything between turns, silently.

This file wires a real ``InMemoryProvider`` into the runner,
captures the second-run LLM request, and asserts the prior turn's
text appears in the messages the model sees.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agentic_primitives_gateway.agents.runner import AgentRunner
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import set_authenticated_principal
from agentic_primitives_gateway.models.agents import AgentSpec, HooksConfig, PrimitiveConfig
from agentic_primitives_gateway.primitives.memory.in_memory import InMemoryProvider

_RUNNER_MOD = "agentic_primitives_gateway.agents.runner"
_ALICE = AuthenticatedPrincipal(id="alice", type="user")


@pytest.fixture(autouse=True)
def _principal():
    set_authenticated_principal(_ALICE)
    yield
    set_authenticated_principal(None)  # type: ignore[arg-type]


def _spec() -> AgentSpec:
    return AgentSpec(
        name="chatbot",
        model="m",
        system_prompt="You are a chatbot.",
        primitives={"memory": PrimitiveConfig(enabled=True)},
        hooks=HooksConfig(auto_memory=True, auto_trace=False),
    )


def _mk_response(content: str) -> dict[str, Any]:
    return {
        "model": "m",
        "content": content,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class TestAutoMemoryRoundTrip:
    @pytest.mark.asyncio
    async def test_prior_turn_reaches_next_session_llm_request(self):
        """Run agent in session S1 → next run in S1 → LLM receives the
        prior turn's content in its ``messages`` array.

        The test captures every LLM request so we can inspect exactly
        what the model was asked.  A break in the load-history path
        surfaces as "content from turn 1 not present in turn 2's
        messages" — an observable, user-visible regression.
        """
        memory = InMemoryProvider()
        captured_requests: list[dict[str, Any]] = []

        async def _capture(request: dict[str, Any]) -> dict[str, Any]:
            # Deep-copy the messages list because the runner mutates
            # it as the loop progresses; we want a snapshot at call
            # time, not at test-assertion time.
            captured_requests.append(
                {
                    "messages": [dict(m) for m in request.get("messages", [])],
                    "system": request.get("system"),
                }
            )
            turn = len(captured_requests)
            return _mk_response(f"assistant-turn-{turn}")

        llm = AsyncMock()
        llm.route_request.side_effect = _capture

        runner = AgentRunner()
        spec = _spec()
        session_id = "session-xyz"

        with patch(f"{_RUNNER_MOD}.registry") as reg:
            reg.llm = llm
            reg.memory = memory
            reg.observability = AsyncMock()

            await runner.run(spec, message="my name is Alice", session_id=session_id)
            await runner.run(spec, message="what is my name?", session_id=session_id)

        assert len(captured_requests) == 2, f"Expected 2 LLM calls, got {len(captured_requests)}"

        # First call: only the user's first message (plus any memory
        # context injection, which doesn't apply here because memory
        # is empty on first run).
        first_msgs = captured_requests[0]["messages"]
        first_user = [m for m in first_msgs if m.get("role") == "user"]
        assert any("my name is Alice" in str(m.get("content", "")) for m in first_user)

        # Second call: the auto-memory hook must have persisted
        # turn 1 AND re-loaded it into the message history.  The
        # prior user text OR the prior assistant text should be in
        # ``messages`` — otherwise the model has amnesia.
        second_msgs = captured_requests[1]["messages"]
        rendered = "\n".join(str(m.get("content", "")) for m in second_msgs)
        assert "my name is Alice" in rendered, (
            f"Second turn's LLM request did not contain prior user text.  "
            f"Auto-memory hook stored the turn but the next session did not "
            f"re-hydrate it.  Messages seen: {second_msgs}"
        )
        assert "assistant-turn-1" in rendered, (
            f"Second turn's LLM request did not contain prior assistant text. Messages seen: {second_msgs}"
        )

        # Sanity: the backing InMemoryProvider actually has the event
        # — confirms create_event landed real data, not just a mock.
        # actor_id format is {owner_id}:{name}:u:{principal.id}
        # (resolve_actor_id in agents/namespace.py).
        actor_id = f"{spec.owner_id}:{spec.name}:u:alice"
        turns = await memory.get_last_turns(actor_id=actor_id, session_id=session_id, k=10)
        assert len(turns) >= 1, (
            f"InMemoryProvider has no events for {actor_id} / {session_id}.  auto_memory hook didn't persist turn 1."
        )

    @pytest.mark.asyncio
    async def test_auto_memory_disabled_does_not_load_prior_turns(self):
        """Sanity check on the contract: when ``auto_memory=False``
        the second session does NOT see turn 1.  Guards against the
        inverse regression (accidentally always loading history).
        """
        memory = InMemoryProvider()
        captured: list[list[dict[str, Any]]] = []

        async def _capture(request: dict[str, Any]) -> dict[str, Any]:
            captured.append([dict(m) for m in request.get("messages", [])])
            return _mk_response(f"reply-{len(captured)}")

        llm = AsyncMock()
        llm.route_request.side_effect = _capture

        spec = AgentSpec(
            name="chatbot",
            model="m",
            system_prompt="x",
            primitives={"memory": PrimitiveConfig(enabled=True)},
            hooks=HooksConfig(auto_memory=False, auto_trace=False),
        )

        runner = AgentRunner()
        with patch(f"{_RUNNER_MOD}.registry") as reg:
            reg.llm = llm
            reg.memory = memory
            reg.observability = AsyncMock()
            await runner.run(spec, message="remember this secret", session_id="s1")
            await runner.run(spec, message="what did I just say?", session_id="s1")

        second = captured[1]
        rendered = "\n".join(str(m.get("content", "")) for m in second)
        assert "remember this secret" not in rendered, (
            f"auto_memory=False should NOT load prior turns.  Found prior user text in second request: {second}"
        )
