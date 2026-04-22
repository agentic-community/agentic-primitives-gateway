"""Intent-level test: parallel tool calls round-trip through Bedrock translation.

The LLM tool-call loop depends on the tool-call ``id`` field to route
tool-result replies back to the correct tool invocation.  When an
LLM returns multiple parallel tool calls in one response, the
gateway must:

1. Preserve each tool call's ``id`` on the way out (Bedrock → internal).
2. Preserve each tool call's order + ``id`` on the way back in
   (internal → Bedrock, when the assistant's message is re-sent
   together with tool_results in the next turn).
3. Route tool_results by ``tool_use_id`` — each result pairs with
   the exact call that produced it.

Existing tests in ``test_bedrock.py``:
- ``test_assistant_tool_call_with_multiple_tools`` checks that two
  tool calls emerge in the right ORDER going to Bedrock (by name),
  but doesn't verify IDs round-trip.
- ``test_tool_use_response`` / ``test_mixed_text_and_tool_use``
  only test single-tool responses.
- No test verifies: LLM returns [t1, t2, t3] → internal preserves
  all three IDs in order → sending back tool_results for t1/t2/t3
  serializes to Bedrock in order.

Breakage mode these tests would catch: a regression where
``_from_bedrock_response`` dedupes by name, reorders by ID
alphabetically, or silently drops empty-input tool calls — any of
which would misroute tool results and produce silently wrong agent
behavior (model answers with data from the wrong tool).
"""

from __future__ import annotations

from agentic_primitives_gateway.primitives.llm.bedrock import (
    _from_bedrock_response,
    _to_bedrock_messages,
)


class TestParallelToolCallsFromBedrock:
    """Bedrock response → internal: multiple tool calls preserved."""

    def test_three_parallel_tool_calls_preserve_ids_and_order(self):
        """Bedrock returns three tool_use blocks.  Internal format
        must carry all three IDs, names, and inputs — in order.
        """
        response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "call_1",
                                "name": "search",
                                "input": {"q": "alpha"},
                            }
                        },
                        {
                            "toolUse": {
                                "toolUseId": "call_2",
                                "name": "weather",
                                "input": {"city": "Paris"},
                            }
                        },
                        {
                            "toolUse": {
                                "toolUseId": "call_3",
                                "name": "search",  # same name, different call
                                "input": {"q": "beta"},
                            }
                        },
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 0, "outputTokens": 0},
        }
        result = _from_bedrock_response(response, "m")

        assert "tool_calls" in result
        tcs = result["tool_calls"]
        assert len(tcs) == 3, f"Expected 3 tool calls, got {len(tcs)}"

        # IDs preserved, in order.
        assert [tc["id"] for tc in tcs] == ["call_1", "call_2", "call_3"]
        assert [tc["name"] for tc in tcs] == ["search", "weather", "search"]
        # Inputs paired with the right ID — critical for correct routing.
        by_id = {tc["id"]: tc for tc in tcs}
        assert by_id["call_1"]["input"] == {"q": "alpha"}
        assert by_id["call_2"]["input"] == {"city": "Paris"}
        assert by_id["call_3"]["input"] == {"q": "beta"}

    def test_tool_calls_with_similar_names_do_not_collapse(self):
        """Two calls to the same tool in one response → both preserved.
        A regression that deduped by name would merge them silently.
        """
        response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "a",
                                "name": "lookup",
                                "input": {"key": "one"},
                            }
                        },
                        {
                            "toolUse": {
                                "toolUseId": "b",
                                "name": "lookup",
                                "input": {"key": "two"},
                            }
                        },
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 0, "outputTokens": 0},
        }
        result = _from_bedrock_response(response, "m")
        assert len(result["tool_calls"]) == 2, "Same-name calls must not be deduped"
        ids = [tc["id"] for tc in result["tool_calls"]]
        assert ids == ["a", "b"]

    def test_tool_call_with_empty_input_preserved(self):
        """A tool call with empty input still needs its ID carried
        through.  Guard against a ``if tc.input:`` filter that would
        silently drop it.
        """
        response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "noargs",
                                "name": "get_time",
                                "input": {},
                            }
                        },
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 0, "outputTokens": 0},
        }
        result = _from_bedrock_response(response, "m")
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["id"] == "noargs"
        assert result["tool_calls"][0]["input"] == {}


class TestParallelToolCallsRoundTrip:
    """Full round-trip: internal → Bedrock → internal preserves IDs."""

    def test_multi_call_assistant_message_serializes_in_order(self):
        """Internal: assistant message with 3 tool_calls → Bedrock
        message with 3 toolUse blocks in same order, same IDs.
        """
        model_request = {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "x1", "name": "a", "input": {"v": 1}},
                        {"id": "x2", "name": "b", "input": {"v": 2}},
                        {"id": "x3", "name": "c", "input": {"v": 3}},
                    ],
                },
            ]
        }
        _, messages = _to_bedrock_messages(model_request)
        assert len(messages) == 1
        blocks = messages[0]["content"]
        assert len(blocks) == 3
        assert [b["toolUse"]["toolUseId"] for b in blocks] == ["x1", "x2", "x3"]
        assert [b["toolUse"]["name"] for b in blocks] == ["a", "b", "c"]

    def test_tool_results_paired_with_original_ids(self):
        """The next turn's tool_results must reference the exact
        IDs emitted by the assistant's tool_calls.  If translation
        mangles IDs, the model can't correlate results to calls.
        """
        # Step 1: LLM returned 2 parallel calls.
        response = {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"toolUse": {"toolUseId": "A1", "name": "f", "input": {}}},
                        {"toolUse": {"toolUseId": "A2", "name": "g", "input": {}}},
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 0, "outputTokens": 0},
        }
        internal = _from_bedrock_response(response, "m")
        ids = [tc["id"] for tc in internal["tool_calls"]]

        # Step 2: next turn's message carries results for each ID.
        next_request = {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": internal["tool_calls"],
                },
                {
                    "tool_results": [
                        {"tool_use_id": ids[0], "content": "result-of-A1"},
                        {"tool_use_id": ids[1], "content": "result-of-A2"},
                    ]
                },
            ]
        }
        _, messages = _to_bedrock_messages(next_request)
        # First message: the two tool_use blocks, IDs intact.
        first_blocks = messages[0]["content"]
        assert [b["toolUse"]["toolUseId"] for b in first_blocks] == ["A1", "A2"]
        # Second message: the two tool_result blocks, referencing the same IDs.
        second_blocks = messages[1]["content"]
        assert [b["toolResult"]["toolUseId"] for b in second_blocks] == ["A1", "A2"]
        # Content correctly paired.
        assert second_blocks[0]["toolResult"]["content"][0]["text"] == "result-of-A1"
        assert second_blocks[1]["toolResult"]["content"][0]["text"] == "result-of-A2"

    def test_out_of_order_tool_results_retain_their_id(self):
        """If the worker executes tools out of order and returns
        results in a different order than the calls, each result's
        ``tool_use_id`` must still map to the correct original call.
        """
        request = {
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "first", "name": "a", "input": {}},
                        {"id": "second", "name": "b", "input": {}},
                    ],
                },
                {
                    "tool_results": [
                        # Deliberately reversed.
                        {"tool_use_id": "second", "content": "B-done"},
                        {"tool_use_id": "first", "content": "A-done"},
                    ]
                },
            ]
        }
        _, messages = _to_bedrock_messages(request)
        result_blocks = messages[1]["content"]
        # IDs preserved in the same order we sent them — the model
        # sees Bedrock's toolResult blocks in that order.
        assert [b["toolResult"]["toolUseId"] for b in result_blocks] == ["second", "first"]
        assert result_blocks[0]["toolResult"]["content"][0]["text"] == "B-done"
        assert result_blocks[1]["toolResult"]["content"][0]["text"] == "A-done"
