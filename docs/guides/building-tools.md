# Building Tools for Your Agent

There are three ways to integrate gateway primitives with your agent framework, from highest to lowest level. All three use the same gateway and backend providers — pick the approach that fits your needs, or mix them.

## 1. Auto-Built Tools

**Best for:** Getting started quickly, using all tools from a primitive, standard behavior.

The client's `get_tools_sync()` / `get_tools()` fetches the tool catalog from the gateway and returns framework-ready callables. Use `format="strands"` or `format="langchain"` for native integration, or omit for plain functions.

### Strands

```python
from agentic_primitives_gateway_client import AgenticPlatformClient
from strands import Agent

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)

# Auto-build Strands-native tools from the gateway catalog
tools = client.get_tools_sync(
    ["memory", "browser"],
    namespace="agent:my-agent",
    format="strands",
)

agent = Agent(model="us.anthropic.claude-sonnet-4-20250514-v1:0", tools=tools)
agent("Remember that Python was created by Guido van Rossum")
```

### LangChain

```python
from agentic_primitives_gateway_client import AgenticPlatformClient
from langgraph.prebuilt import create_react_agent
from langchain_aws import ChatBedrock

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)

# Auto-build LangChain StructuredTool instances
tools = await client.get_tools(
    ["memory"],
    namespace="agent:my-agent",
    format="langchain",
)

agent = create_react_agent(ChatBedrock(model_id="..."), tools=tools)
```

### Plain functions (any framework)

```python
tools = client.get_tools_sync(["memory"], namespace="agent:my-agent")
# tools are plain callables with tool_spec, tool_name, __doc__ attributes
# Wrap in whatever your framework needs
```

## 2. Manual `@tool` Wrappers

**Best for:** Custom tool descriptions, combining multiple API calls, adding validation, controlling exactly what the LLM sees.

Create your own tool functions that call the gateway client. You control the description, parameter names, error handling, and return format.

### LangChain

```python
from langchain_core.tools import tool
from agentic_primitives_gateway_client import AgenticPlatformClient, Memory

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
memory = Memory(client, namespace="agent:my-agent")

@tool
async def remember(key: str, content: str) -> str:
    """Store a fact for later. Keys should be descriptive (e.g., 'user-preference-color')."""
    return await memory.remember(key, content)

@tool
async def search_memory(query: str) -> str:
    """Search your memory for relevant information before answering."""
    return await memory.search(query)
```

### Strands

```python
from strands.tools import tool
from agentic_primitives_gateway_client import AgenticPlatformClient, Memory

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
memory = Memory(client, namespace="agent:my-agent")

@tool
def remember(key: str, content: str) -> str:
    """Store important information for later recall."""
    return memory.remember_sync(key, content)
```

### When to use manual wrappers

- You want a **custom description** that guides the LLM better than the catalog default
- You need to **combine multiple calls** (e.g., search memory + format results)
- You want to **add validation** before calling the gateway
- You want to **filter or transform** the response before returning to the LLM

## 3. Primitive Client Objects

**Best for:** Auto-memory, injecting context into prompts, non-tool workflows.

Use the typed client classes (`Memory`, `Browser`, `CodeInterpreter`, `Observability`) directly in your agent loop. These aren't tools the LLM calls — they're infrastructure you call from your code.

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Memory, Observability

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
memory = Memory(client, namespace="agent:my-agent")
obs = Observability(client, namespace="agent:my-agent")

# Before each turn: recall relevant context and inject into the system prompt
context = await memory.recall_context(user_input)
system_prompt = f"You are a helpful assistant.\n\nRelevant context:\n{context}"

# After each turn: store the conversation
await memory.store_turn(user_input, response)

# Trace the conversation for observability
await obs.trace("conversation:turn", {"user": user_input}, response)
```

### When to use primitive objects

- **Auto-memory**: store every conversation turn transparently, recall context on each message
- **Observability**: trace and score LLM calls without the agent knowing
- **Session management**: start/stop browser or code interpreter sessions programmatically
- **Batch operations**: list, delete, or migrate memories outside of an agent conversation

## Mixing Approaches

You can combine all three in a single agent:

```python
# Auto-build browser tools (approach 1)
browser_tools = client.get_tools_sync(["browser"], format="strands")

# Custom memory tool with better description (approach 2)
@tool
def smart_remember(key: str, content: str) -> str:
    """Store a fact. Always use descriptive keys like 'user-name' or 'project-deadline'."""
    return memory.remember_sync(key, content)

# Auto-memory for transparent context (approach 3)
context = memory.recall_context_sync(user_input)

agent = Agent(
    model="...",
    system_prompt=f"You are helpful.\n\nContext:\n{context}",
    tools=[smart_remember, *browser_tools],
)
```
