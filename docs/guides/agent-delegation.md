# Agent-as-Tool Delegation

Agents can call other agents as tools, enabling coordinator patterns where a supervisor delegates tasks to specialized workers.

## Setup

Define a coordinator agent that lists other agents in its `agents` primitive:

```yaml
agents:
  specs:
    researcher:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      description: "Researches topics using memory and web"
      primitives:
        memory: { enabled: true }
        browser: { enabled: true }

    coder:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      description: "Writes and executes code"
      primitives:
        code_interpreter: { enabled: true }

    coordinator:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      description: "Delegates to researcher and coder"
      system_prompt: |
        You coordinate specialized agents. Delegate tasks by calling
        call_researcher or call_coder. Make parallel calls when tasks
        are independent.
      primitives:
        memory: { enabled: true }
        agents:
          enabled: true
          tools: ["researcher", "coder"]
```

## What the Coordinator Sees

The coordinator's LLM receives these tools:

| Tool | Description |
|------|-------------|
| `call_researcher(message)` | Delegate a task to the 'researcher' agent |
| `call_coder(message)` | Delegate a task to the 'coder' agent |
| `remember(key, content)` | Store information (from memory primitive) |

## How It Works

1. Coordinator calls `call_researcher(message="What are the top 3 Python frameworks?")`
2. The gateway runs `AgentRunner.run()` for the researcher agent (with incremented depth)
3. The researcher uses its own tools (memory, browser) to answer
4. The researcher's response + tool artifacts are returned to the coordinator
5. The coordinator synthesizes and responds to the user

## Parallel Delegation

The coordinator can call multiple agents simultaneously:

```
Coordinator LLM response:
  tool_use: call_researcher(message="Research Python frameworks")
  tool_use: call_coder(message="Write a hello world benchmark")

Both run in parallel via asyncio.gather
```

The coordinator's system prompt should encourage this:
```
When tasks are independent, call multiple agents in parallel
by making multiple tool calls in the same response.
```

## Depth Limiting

To prevent infinite recursion (A calls B calls A), delegation depth is tracked:

- `MAX_AGENT_DEPTH = 3`
- Each delegation increments depth
- At max depth, the agent returns an error message instead of running

## Streaming

In streaming mode, sub-agent events are forwarded to the parent:

| Event | Description |
|-------|-------------|
| `sub_agent_token` | Token from a sub-agent (streamed to UI) |
| `sub_agent_tool` | Sub-agent using a tool (e.g., "using execute_code") |
| `tool_call_result` | Delegation complete (triggers "done" state in UI) |

The UI shows collapsible sub-agent activity blocks with real-time content.

## Artifacts

When a sub-agent uses tools (e.g., `execute_code`), the code and output are captured as artifacts and returned to the coordinator. This ensures the coordinator LLM has the actual code, not just a summary.

## Provider Overrides

Each agent uses its own `provider_overrides`. When the coordinator delegates to the researcher:

1. The coordinator's overrides are saved
2. The researcher's overrides are merged on top
3. After the researcher finishes, the coordinator's overrides are restored

This ensures each agent talks to its configured providers.

## vs Agent Teams

| Feature | Agent Delegation | Agent Teams |
|---------|-----------------|-------------|
| Coordination | Direct (coordinator calls sub-agents) | Task board (planner decomposes) |
| Parallelism | Coordinator decides what to parallelize | Workers claim tasks independently |
| Replanning | Manual (coordinator decides next steps) | Automatic (replanner evaluates results) |
| Best for | Simple 2-3 agent workflows | Complex multi-step collaboration |
