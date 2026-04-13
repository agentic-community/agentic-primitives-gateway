# Self-Hosted Examples

Agents using open-source backends: mem0/Milvus for memory, Langfuse for observability,
Selenium Grid for browser, Jupyter for code execution, Bedrock for LLM.

## Prerequisites

```bash
pip install agentic-primitives-gateway-client[aws] strands-agents langchain langchain-aws langgraph

# Start infrastructure
docker run -d --name redis -p 6379:6379 redis:7-alpine
docker run -d --name milvus -p 19530:19530 milvusdb/milvus:latest standalone
docker run -d --name selenium -p 4444:4444 selenium/standalone-chrome
docker run -d --name jupyter -p 8888:8888 jupyter/minimal-notebook

# Set Langfuse credentials (from your Langfuse project settings)
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...

# Start the gateway
./run.sh selfhosted
```

## What's demonstrated

| Primitive | Backend | What the examples do |
|---|---|---|
| Memory | mem0 + Milvus | Semantic vector search — natural language queries work |
| Browser | Selenium Grid | Real browser automation via Chrome |
| Code Interpreter | Jupyter | Python execution with persistent kernel state |
| Observability | Langfuse | Trace conversations and tool calls |
| LLM | Bedrock | Claude Sonnet for reasoning and tool use |

## Key difference from quickstart

The quickstart uses in-memory storage where `search_memory("what is my name?")` does substring matching. With mem0/Milvus, it does **semantic vector search** — natural language queries find relevant memories even without exact keyword matches.

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
