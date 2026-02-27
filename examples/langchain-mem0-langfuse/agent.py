"""LangChain agent with full mem0/Milvus memory and Langfuse observability.

Demonstrates every feature of the memory and observability primitives
through the open-source stack:

  Memory (mem0 + Milvus):
    - Key-value storage with semantic search
    - Transparent auto-memory (store every turn, recall context)
    - Conversation events and session history
    - Branching conversations

  Observability (Langfuse):
    - Trace ingestion with custom trace IDs
    - LLM generation logging with token usage
    - Evaluation scoring
    - Trace querying and session management
    - Automatic tracing of all tool calls

  Browser (Selenium Grid):
    - Navigate, screenshot, read page content
    - Click elements, type into inputs, run JavaScript

Server config:
    ./run.sh milvus-langfuse

Prerequisites:
    pip install -r requirements.txt

    # Milvus must be running (e.g., via Docker)
    docker run -d --name milvus -p 19530:19530 milvusdb/milvus:latest

    # Selenium Grid for browser
    docker run -d --name selenium -p 4444:4444 -p 7900:7900 --shm-size="2g" selenium/standalone-chrome:latest

Usage:
    export LANGFUSE_PUBLIC_KEY=pk-lf-...
    export LANGFUSE_SECRET_KEY=sk-lf-...
    export LANGFUSE_BASE_URL=http://localhost:3000  # or https://cloud.langfuse.com

    python agent.py
"""

import asyncio
import os
import sys
from uuid import uuid4

from langchain.agents import create_agent
from langchain_aws import ChatBedrock
from langchain_core.tools import tool

from agentic_primitives_gateway_client import AgenticPlatformClient, Browser, Memory, Observability

# ── Platform client ─────────────────────────────────────────────────

# ── Configuration ─────────────────────────────────────────────────

GATEWAY_URL = "http://localhost:8000"
SELENIUM_HOST = "localhost"
SELENIUM_PORT = 61576

# ── Platform client ─────────────────────────────────────────────────

platform = AgenticPlatformClient(
    GATEWAY_URL,
    aws_from_environment=True,
)

# Route browser to Selenium Grid
platform.set_provider_for("browser", "selenium_grid")
SELENIUM_HUB_URL = f"http://{SELENIUM_HOST}:{SELENIUM_PORT}"
platform.set_service_credentials("selenium", {"hub_url": SELENIUM_HUB_URL})
print(f"Selenium Grid: {SELENIUM_HUB_URL}")

# Langfuse credentials
if os.environ.get("LANGFUSE_PUBLIC_KEY"):
    platform.set_service_credentials(
        "langfuse",
        {
            "public_key": os.environ["LANGFUSE_PUBLIC_KEY"],
            "secret_key": os.environ["LANGFUSE_SECRET_KEY"],
            "base_url": os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com"),
        },
    )
    print(f"Langfuse: tracing to {os.environ.get('LANGFUSE_BASE_URL', 'cloud.langfuse.com')}")
else:
    print("WARNING: LANGFUSE_PUBLIC_KEY not set — observability will use noop")

AGENT_NAMESPACE = "agent:langchain-full"
SESSION_ID = f"session-{uuid4().hex[:8]}"
ACTOR_ID = "langchain-agent"

obs = Observability(
    platform,
    namespace=AGENT_NAMESPACE,
    session_id=SESSION_ID,
    tags=["langchain-agent", "mem0", "langfuse"],
)

memory = Memory(
    platform,
    namespace=AGENT_NAMESPACE,
    session_id=SESSION_ID,
    observability=obs,
)

browser = Browser(platform)


# ── Memory: key-value tools ────────────────────────────────────────


@tool
async def remember(key: str, content: str, source: str = "") -> str:
    """Store information in long-term memory with semantic indexing.

    Args:
        key: A short identifier (e.g., "user-preference", "project-deadline").
        content: The information to remember.
        source: Optional source of the information.
    """
    return await memory.remember(key, content, source)


@tool
async def recall(key: str) -> str:
    """Retrieve a specific memory by its exact key.

    Args:
        key: The key to look up.
    """
    return await memory.recall(key)


@tool
async def search_memory(query: str, top_k: int = 5) -> str:
    """Search memories using semantic vector similarity (Milvus).

    Args:
        query: Natural language description of what you're looking for.
        top_k: Maximum number of results.
    """
    return await memory.search(query, top_k)


@tool
async def list_memories(limit: int = 20) -> str:
    """List all stored memories in this namespace.

    Args:
        limit: Maximum number to show.
    """
    return await memory.list(limit)


@tool
async def forget(key: str) -> str:
    """Delete a memory permanently.

    Args:
        key: The key of the memory to delete.
    """
    return await memory.forget(key)


# ── Memory: conversation events ────────────────────────────────────


@tool
async def add_message(role: str, content: str) -> str:
    """Record a message in the conversation event log.

    Use this to explicitly add messages that should be part of the
    persistent conversation history (beyond auto-memory).

    Args:
        role: Message role ('user' or 'assistant').
        content: The message text.
    """
    return await memory.add_message(role, content)


@tool
async def get_conversation_history(turns: int = 5) -> str:
    """Get the last K conversation turns from the event log.

    Args:
        turns: Number of recent turns to retrieve.
    """
    return await memory.get_history(turns)


@tool
async def list_sessions() -> str:
    """List all conversation sessions for this agent."""
    return await memory.list_conversations()


# ── Memory: branching ──────────────────────────────────────────────


@tool
async def fork_conversation(root_event_id: str, branch_name: str) -> str:
    """Fork the conversation from a specific event to explore alternatives.

    Args:
        root_event_id: The event ID to branch from.
        branch_name: A name for the new branch (e.g., "alternative-approach").
    """
    result = await platform.fork_conversation(ACTOR_ID, SESSION_ID, root_event_id, branch_name, [])
    await obs.trace("memory:fork", {"root_event_id": root_event_id, "branch": branch_name}, str(result))
    return f"Forked conversation at event {root_event_id} as '{branch_name}'"


@tool
async def list_branches() -> str:
    """List all branches of the current conversation session."""
    result = await platform.list_branches(ACTOR_ID, SESSION_ID)
    branches = result.get("branches", [])
    if not branches:
        return "No branches exist for this session."
    lines = [f"  {b.get('branch_id', '?')}: {b.get('branch_name', '?')}" for b in branches]
    return f"{len(branches)} branches:\n" + "\n".join(lines)


# ── Browser tools (Selenium Grid) ─────────────────────────────────


@tool
async def open_browser() -> str:
    """Start a Selenium Grid browser session."""
    result = await browser.start()
    await obs.trace("browser:start", {}, result)
    return result


@tool
async def close_browser() -> str:
    """Close the current browser session."""
    result = await browser.close()
    await obs.trace("browser:stop", {}, result)
    return result


@tool
async def browse_to(url: str) -> str:
    """Navigate the browser to a URL.

    Args:
        url: The URL to navigate to.
    """
    result = await browser.navigate(url)
    await obs.trace("browser:navigate", {"url": url}, result)
    return result


@tool
async def read_page() -> str:
    """Read the text content of the current page."""
    result = await browser.get_page_content()
    await obs.trace("browser:read_page", {}, result[:200])
    return result


@tool
async def click_element(selector: str) -> str:
    """Click an element on the page.

    Args:
        selector: CSS selector (e.g., "button.submit", "#login").
    """
    result = await browser.click(selector)
    await obs.trace("browser:click", {"selector": selector}, result)
    return result


@tool
async def type_into(selector: str, text: str) -> str:
    """Type text into an input field.

    Args:
        selector: CSS selector of the input.
        text: Text to type.
    """
    result = await browser.type_text(selector, text)
    await obs.trace("browser:type", {"selector": selector}, result)
    return result


@tool
async def take_screenshot() -> str:
    """Take a screenshot of the current browser page."""
    result = await browser.screenshot()
    await obs.trace("browser:screenshot", {}, "screenshot captured")
    return result


@tool
async def run_js(expression: str) -> str:
    """Run JavaScript in the browser and return the result.

    Args:
        expression: JavaScript expression to evaluate.
    """
    result = await browser.evaluate(expression)
    await obs.trace("browser:evaluate", {"expression": expression[:200]}, result[:500])
    return result


# ── Observability: Langfuse features ──────────────────────────────


@tool
async def query_traces(limit: int = 10) -> str:
    """Query recent traces from Langfuse.

    Args:
        limit: Maximum number of traces to return.
    """
    return await obs.query_traces(limit)


@tool
async def get_trace(trace_id: str) -> str:
    """Get detailed information about a specific trace.

    Args:
        trace_id: The trace ID to look up.
    """
    return await obs.get_trace(trace_id)


@tool
async def log_llm_generation(
    trace_id: str,
    model: str,
    prompt: str,
    completion: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> str:
    """Log an LLM generation to Langfuse for cost and quality tracking.

    Args:
        trace_id: The parent trace ID.
        model: Model identifier (e.g., "claude-3-sonnet").
        prompt: The input prompt.
        completion: The model's response.
        prompt_tokens: Number of input tokens.
        completion_tokens: Number of output tokens.
    """
    usage = {}
    if prompt_tokens:
        usage["prompt_tokens"] = prompt_tokens
    if completion_tokens:
        usage["completion_tokens"] = completion_tokens
    return await obs.log_llm_call(trace_id, "llm-generation", model, prompt, completion, usage or None)


@tool
async def score_trace(trace_id: str, name: str, value: float, comment: str = "") -> str:
    """Attach an evaluation score to a Langfuse trace.

    Args:
        trace_id: The trace to score.
        name: Score dimension (e.g., "accuracy", "helpfulness", "relevance").
        value: Numeric score (0.0 to 1.0).
        comment: Optional explanation.
    """
    return await obs.score(trace_id, name, value, comment or None)


@tool
async def view_sessions(limit: int = 10) -> str:
    """List recent Langfuse observability sessions.

    Args:
        limit: Maximum number of sessions.
    """
    return await obs.get_sessions(limit)


# ── Agent ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a research assistant with persistent memory backed by mem0 + \
Milvus, a browser via Selenium Grid, and full observability through Langfuse.

**Memory** (mem0 + Milvus vector search):
- `remember` — store with semantic indexing
- `search_memory` — find via vector similarity
- `recall` — retrieve by exact key
- `list_memories` — see everything stored
- `forget` — remove outdated information
- `add_message`, `get_conversation_history` — event-based conversation log
- `list_sessions` — see all conversation sessions
- `fork_conversation`, `list_branches` — branch conversations

**Browser** (Selenium Grid — self-hosted):
- `open_browser` — start a browser session
- `browse_to` — navigate to a URL
- `read_page` — read the text content of the current page
- `click_element` — click a button, link, or other element (CSS selector)
- `type_into` — type text into an input field (CSS selector)
- `run_js` — run JavaScript on the page
- `take_screenshot` — capture a screenshot
- `close_browser` — stop the session when done

**Observability** (Langfuse):
- `query_traces` — see recent activity
- `get_trace` — inspect a specific trace
- `log_llm_generation` — log LLM calls with token usage
- `score_trace` — attach evaluation scores to traces
- `view_sessions` — see observability sessions
- All your tool calls are automatically traced to Langfuse

Always search your memory before saying you don't know something. \
When you learn new information, store it. Your memory persists across \
sessions — if a user told you something yesterday, you can recall it today. \
When using the browser, always start with `open_browser`, then `browse_to` \
a URL. Use `close_browser` when done.
"""


async def main():
    model = ChatBedrock(model_id="us.anthropic.claude-sonnet-4-20250514-v1:0")

    agent = create_agent(
        model,
        tools=[
            # Memory: key-value
            remember,
            recall,
            search_memory,
            list_memories,
            forget,
            # Memory: conversation
            add_message,
            get_conversation_history,
            list_sessions,
            # Memory: branching
            fork_conversation,
            list_branches,
            # Browser (Selenium Grid)
            open_browser,
            close_browser,
            browse_to,
            read_page,
            click_element,
            type_into,
            take_screenshot,
            run_js,
            # Observability
            query_traces,
            get_trace,
            log_llm_generation,
            score_trace,
            view_sessions,
        ],
        system_prompt=SYSTEM_PROMPT,
    )

    print("LangChain + mem0/Milvus + Selenium + Langfuse agent ready.")
    print(f"Namespace: {AGENT_NAMESPACE}")
    print(f"Session: {SESSION_ID}")
    print(f"Langfuse session: {obs.session_id}")
    print("Type 'quit' to exit.\n")

    await obs.log("info", "LangChain mem0+langfuse agent started")

    history = []

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        # Auto-memory: store the user's turn
        await memory.store_turn("user", user_input)

        # Recall relevant context
        context = await memory.recall_context(user_input)

        # Build prompt with memory context
        system_prompt = SYSTEM_PROMPT
        if context:
            system_prompt += "\n\nRelevant context from past conversations:\n" + context

        messages = [{"role": "system", "content": system_prompt}, *history]
        messages.append({"role": "user", "content": user_input})

        # Stream the response
        sys.stdout.write("\nAssistant: ")
        sys.stdout.flush()
        reply_chunks: list[str] = []
        async for event in agent.astream_events({"messages": messages}, version="v2"):
            if event["event"] == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk and hasattr(chunk, "content"):
                    content = chunk.content
                    if isinstance(content, str) and content:
                        sys.stdout.write(content)
                        sys.stdout.flush()
                        reply_chunks.append(content)
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("text"):
                                sys.stdout.write(block["text"])
                                sys.stdout.flush()
                                reply_chunks.append(block["text"])

        reply = "".join(reply_chunks)
        sys.stdout.write("\n\n")
        sys.stdout.flush()

        if reply:
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": reply})

            # Auto-memory: store the assistant's turn
            await memory.store_turn("assistant", reply)

            # Trace the full conversation turn
            await obs.trace(
                "conversation:turn",
                {"user": user_input},
                reply,
                tags=["conversation"],
            )
        else:
            print("\nAssistant: (no response)\n")

    await browser.close()
    await obs.flush()
    await obs.log("info", "LangChain mem0+langfuse agent stopped")


if __name__ == "__main__":
    asyncio.run(main())
