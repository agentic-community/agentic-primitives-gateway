# Quickstart Examples

Minimal examples showing how to use the gateway from different perspectives. All examples assume the gateway is running at `http://localhost:8000` (`./run.sh`).

## Examples

| File | What it shows |
|---|---|
| `with_curl.sh` | Raw REST API calls — no SDK, no framework. Proves it's just HTTP. |
| `plain_python.py` | Plain Python + boto3 + gateway client. No agent framework. |
| `with_strands.py` | Strands agent using auto-built tools from the gateway catalog. |
| `with_langchain.py` | LangChain agent with manual `@tool` wrappers around the `Memory` client. |

## Three ways to build tools

These examples demonstrate different integration depths:

| Approach | Example | When to use |
|---|---|---|
| **REST API** | `with_curl.sh` | Any language, maximum control, no SDK needed |
| **Auto-built tools** | `with_strands.py` | Fastest setup — `get_tools_sync(format="strands")` returns ready-to-use tools |
| **Manual wrappers** | `with_langchain.py` | Full control over tool behavior — wrap `Memory` / `Observability` clients with framework-specific decorators |

## Key point

The gateway is a REST API. The examples above use Python, but any language works. The same gateway serves all of them simultaneously — swap the client, keep the infrastructure.

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   curl / HTTP    │     │    LangChain    │     │     Strands     │
│                  │     │                  │     │                  │
│  POST /memory/…  │     │  @tool remember  │     │  agent(tools=…)  │
└────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘
         │                        │                        │
         └────────────────────────┼────────────────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │   Agentic Primitives Gateway │
                    │   (same server, same config) │
                    └─────────────┬──────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
        ┌─────▼─────┐     ┌──────▼──────┐     ┌──────▼──────┐
        │  Memory    │     │   Browser   │     │    Code     │
        │ (Milvus/   │     │ (Selenium/  │     │ (Jupyter/   │
        │ AgentCore) │     │ AgentCore)  │     │ AgentCore)  │
        └────────────┘     └─────────────┘     └─────────────┘
```
