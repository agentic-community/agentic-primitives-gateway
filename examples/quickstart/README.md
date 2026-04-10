# Quickstart Examples

Minimal examples showing how to use the gateway from different perspectives. All examples assume the gateway is running at `http://localhost:8000` (`./run.sh`).

## Examples

| File | What it shows |
|---|---|
| `with_curl.sh` | Raw REST API calls — no SDK, no framework. Proves it's just HTTP. |
| `plain_python.py` | Plain Python + boto3 + gateway client. No agent framework. |
| `with_langchain.py` | LangChain agent using gateway memory as tools. |
| `with_strands.py` | Strands agent using auto-built tools from the gateway catalog. |

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
