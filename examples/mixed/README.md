# Mixed Provider Examples

Agents using both AgentCore and self-hosted providers simultaneously. Demonstrates
JWT authentication, provider routing, and gradual migration between backends.

## Prerequisites

The self-hosted infrastructure can be run locally with Docker, or deployed via the [Agents on EKS](https://awslabs.github.io/ai-on-eks/docs/infra/agents-on-eks) infrastructure which sets up Milvus, Langfuse, Selenium Grid, Jupyter, Redis, and Keycloak on Kubernetes.

```bash
pip install agentic-primitives-gateway-client[aws] strands-agents langchain langchain-aws langgraph

# Start all infrastructure (selfhosted + AgentCore)
docker run -d --name redis -p 6379:6379 redis:7-alpine
docker run -d --name milvus -p 19530:19530 milvusdb/milvus:latest standalone
docker run -d --name selenium -p 4444:4444 selenium/standalone-chrome
# + Langfuse, Jupyter

# AgentCore memory resource (for the AgentCore memory backend)
export AGENTCORE_MEMORY_ID=memory_xxxx

# OIDC provider for JWT auth
export JWT_ISSUER=https://keycloak.example.com/realms/my-realm
export OIDC_USERNAME=myuser
export OIDC_PASSWORD=mypassword

# Start the gateway
./run.sh mixed
```

## What's demonstrated

| Feature | How it's shown |
|---|---|
| **Dual providers** | mem0 + AgentCore memory both registered, switch via headers |
| **JWT auth** | `fetch_token_from_env()` authenticates from OIDC env vars |
| **Provider routing** | `client.set_provider_for("memory", "agentcore")` switches backend |
| **Credential resolution** | Gateway resolves per-user credentials from JWT attributes |
| **All primitives** | Memory, browser, code, observability, identity, tools, policy |

## Provider switching

The mixed config registers both backends for memory, observability, browser, and code:

```python
# Default: self-hosted (mem0, Selenium, Jupyter, Langfuse)
tools = client.get_tools_sync(["memory"], namespace="...", format="strands")

# Switch memory to AgentCore for this client
client.set_provider_for("memory", "agentcore")

# Or per-request via curl:
# curl -H "X-Provider-Memory: agentcore" ...
```

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
| `with_strands.py` | Strands | Tools-only — JWT auth + auto-built tools |
| `with_langchain.py` | LangChain | Tools + context injection + JWT auth + provider routing |
