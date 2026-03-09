# Declarative Agents

Agents are defined by specs and run server-side LLM tool-call loops. No external agent framework needed.

## Agent Spec

```yaml
agents:
  specs:
    research-assistant:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      description: "A research assistant with long-term memory"
      system_prompt: |
        You are a research assistant with long-term memory.
        Use the remember tool to store important information.
      primitives:
        memory:
          enabled: true
          namespace: "agent:{agent_name}"
        browser:
          enabled: true
      provider_overrides:
        browser: "selenium_grid"
      hooks:
        auto_memory: true
        auto_trace: false
      max_turns: 20
      temperature: 1.0
```

### Fields

| Field | Description | Default |
|-------|-------------|---------|
| `name` | Unique identifier | required |
| `model` | LLM model ID (e.g., Bedrock model ARN) | required |
| `description` | Human-readable description | `""` |
| `system_prompt` | System prompt for the LLM | `"You are a helpful assistant."` |
| `primitives` | Enabled primitives with optional tool filtering | `{}` |
| `provider_overrides` | Per-primitive provider overrides | `{}` |
| `hooks.auto_memory` | Auto-save conversation turns to memory | `true` |
| `hooks.auto_trace` | Auto-trace to observability | `true` |
| `max_turns` | Maximum LLM calls per chat | `20` |
| `temperature` | LLM temperature | `1.0` |

## How It Works

When you call `POST /api/v1/agents/{name}/chat`:

1. **Initialize**: Load conversation history, inject stored memories, build tool list
2. **Loop**: Call LLM → if tool_use, execute tools in parallel → repeat
3. **Finalize**: Store conversation turn, trace, return response

```
User message → [memory context injection] → LLM → tool calls → LLM → response
                                              ↑                    |
                                              +--- tool results ---+
```

## Available Tools

Each enabled primitive provides tools to the LLM:

| Primitive | Tools |
|-----------|-------|
| **memory** | `remember`, `recall`, `search_memory`, `forget`, `list_memories` |
| **code_interpreter** | `execute_code` |
| **browser** | `navigate`, `read_page`, `click`, `type_text`, `screenshot`, `evaluate_js` |
| **tools** | `search_tools`, `invoke_tool` |
| **identity** | `get_token`, `get_api_key` |
| **agents** | `call_{agent_name}` (dynamic, one per sub-agent) |

### Tool Filtering

Limit which tools an agent can use:

```yaml
primitives:
  memory:
    enabled: true
    tools: ["remember", "recall"]  # Only these two, not search/forget/list
```

## Streaming

`POST /api/v1/agents/{name}/chat/stream` returns SSE events:

```
data: {"type": "stream_start", "session_id": "abc123"}
data: {"type": "token", "content": "Hello"}
data: {"type": "token", "content": " there!"}
data: {"type": "tool_call_start", "name": "remember", "id": "tc_1"}
data: {"type": "tool_call_result", "name": "remember", "id": "tc_1", "result": "Stored."}
data: {"type": "token", "content": "I've remembered that."}
data: {"type": "done", "response": "...", "turns_used": 2, "tools_called": ["remember"]}
```

## Agent-as-Tool Delegation

Agents can call other agents. See [Agent Delegation Guide](../guides/agent-delegation.md).

```yaml
coordinator:
  primitives:
    agents:
      enabled: true
      tools: ["researcher", "coder"]  # Names of other agents
```

The coordinator LLM gets `call_researcher(message)` and `call_coder(message)` tools.

## Memory Namespaces

The `namespace` field controls where memories are stored. See [Memory Namespaces Guide](../guides/memory-namespaces.md).

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/agents` | Create agent |
| `GET` | `/api/v1/agents` | List agents |
| `GET` | `/api/v1/agents/{name}` | Get agent spec |
| `PUT` | `/api/v1/agents/{name}` | Update agent |
| `DELETE` | `/api/v1/agents/{name}` | Delete agent |
| `POST` | `/api/v1/agents/{name}/chat` | Chat (non-streaming) |
| `POST` | `/api/v1/agents/{name}/chat/stream` | Chat (SSE streaming) |
| `GET` | `/api/v1/agents/{name}/tools` | List agent's tools with providers |
| `GET` | `/api/v1/agents/{name}/memory` | Introspect memory stores |
| `GET` | `/api/v1/agents/tool-catalog` | List all available primitives/tools |
