# AgentCore Examples

Agents using all AWS managed primitives: AgentCore memory, browser, code interpreter,
identity, tools, observability + Bedrock for LLM.

## Prerequisites

```bash
pip install agentic-primitives-gateway-client[aws] strands-agents langchain langchain-aws langgraph

# Create an AgentCore memory resource in the AWS console
export AGENTCORE_MEMORY_ID=memory_xxxx

# Start the gateway
./run.sh agentcore
```

## What's demonstrated

| Primitive | Backend | What the examples do |
|---|---|---|
| Memory | AgentCore | Store facts, search semantically, recall by key |
| Browser | AgentCore | Navigate pages, read content, interact with elements |
| Code Interpreter | AgentCore | Execute Python, persist state across calls |
| Observability | AgentCore | Trace conversations and tool calls |
| LLM | Bedrock | Claude Sonnet for reasoning and tool use |

## Two approaches to memory

The Strands and LangChain examples demonstrate two valid patterns for using memory:

| Approach | Example | How it works |
|---|---|---|
| **Tools-only** | `with_strands.py` | The agent gets memory as tools and decides when to search/store. Simpler code, relies on the LLM to use memory proactively. |
| **Context injection** | `with_langchain.py` | The `Memory` client automatically searches memory before each turn (RAG-style) and stores conversation history after. Guarantees relevant context is always in the prompt. |

Both use the same gateway primitives — the difference is whether the application or the LLM controls when memory is accessed.

## Examples

| File | Framework | Pattern |
|---|---|---|
| `with_strands.py` | Strands | Tools-only — agent decides when to use memory |
| `with_langchain.py` | LangChain | Tools + automatic context injection via `Memory` client |
