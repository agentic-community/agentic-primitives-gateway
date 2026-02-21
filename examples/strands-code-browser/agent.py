"""Strands agent with code interpreter and browser capabilities.

Demonstrates the code interpreter and browser primitives:
  - Code Interpreter — run Python in a sandboxed session, upload/download files
  - Browser — start cloud browser sessions, get live view URLs

The agent can write and execute code, then use a browser to look things up
or interact with web pages. Both primitives are backed by AgentCore when
the server is configured with agentcore backends.

Server config:
    ./run.sh agentcore       # AgentCore backends
    ./run.sh kitchen-sink    # All backends available

Prerequisites:
    pip install -r requirements.txt

Usage:
    python agent.py
"""

from strands import Agent, tool
from strands.models import BedrockModel

from agentic_primitives_gateway_client import (
    AgenticPlatformClient,
    Browser,
    CodeInterpreter,
    Observability,
)

# ── Platform client ─────────────────────────────────────────────────

platform = AgenticPlatformClient(
    "http://localhost:8000",
    aws_from_environment=True,
)

# Route to agentcore backends when using kitchen-sink config
platform.set_provider_for("code_interpreter", "agentcore")
platform.set_provider_for("browser", "agentcore")
platform.set_provider_for("observability", "agentcore")

AGENT_NAMESPACE = "agent:code-browser"

obs = Observability(platform, namespace=AGENT_NAMESPACE, tags=["strands-agent", "code-browser"])
code = CodeInterpreter(platform)
browser = Browser(platform)


# ── Code Interpreter tools ──────────────────────────────────────────


@tool
def run_python(code_str: str) -> str:
    """Execute Python code in a sandboxed environment.

    The code runs in an isolated container. State (variables, imports,
    files) persists across calls within the session.

    Args:
        code_str: The Python code to execute.
    """
    result = code.execute_sync(code_str)
    obs.trace_sync("code:execute", {"code": code_str[:200]}, result[:500])
    return result


@tool
def run_shell(command: str) -> str:
    """Execute a shell command in the sandboxed environment.

    Args:
        command: The shell command to run.
    """
    # Shell commands go through the code interpreter as Python subprocess
    wrapped = f"""
import subprocess
result = subprocess.run({command!r}, shell=True, capture_output=True, text=True)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
"""
    result = code.execute_sync(wrapped)
    obs.trace_sync("code:shell", {"command": command}, result[:500])
    return result


@tool
def install_package(package: str) -> str:
    """Install a Python package in the sandboxed environment.

    Args:
        package: Package name (e.g., "requests", "pandas").
    """
    result = code.execute_sync(f"""
import subprocess
result = subprocess.run(["pip", "install", "{package}"], capture_output=True, text=True)
print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
if result.returncode != 0:
    print("ERROR:", result.stderr[-500:])
""")
    obs.trace_sync("code:install", {"package": package}, result[:200])
    return result


# ── Browser tools ───────────────────────────────────────────────────


@tool
def open_browser() -> str:
    """Start a cloud browser session.

    Returns session info and a live view URL that can be shared
    for debugging or observation.
    """
    result = browser.start_sync()
    obs.trace_sync("browser:start", {}, result)
    return result


@tool
def close_browser() -> str:
    """Close the current browser session."""
    result = browser.close_sync()
    obs.trace_sync("browser:stop", {}, result)
    return result


@tool
def browse_to(url: str) -> str:
    """Navigate the browser to a URL.

    Args:
        url: The URL to navigate to (e.g., "https://example.com").
    """
    result = browser.navigate_sync(url)
    obs.trace_sync("browser:navigate", {"url": url}, result)
    return result


@tool
def read_page() -> str:
    """Read the text content of the current page in the browser.

    Returns the visible text on the page.
    """
    result = browser.get_page_content_sync()
    obs.trace_sync("browser:read_page", {}, result[:200])
    return result


@tool
def click_element(selector: str) -> str:
    """Click an element on the page.

    Args:
        selector: CSS selector (e.g., "button.submit", "#login", "a[href='/about']").
    """
    result = browser.click_sync(selector)
    obs.trace_sync("browser:click", {"selector": selector}, result)
    return result


@tool
def type_into(selector: str, text: str) -> str:
    """Type text into an input field on the page.

    Args:
        selector: CSS selector of the input (e.g., "input[name='email']", "#search").
        text: The text to type.
    """
    result = browser.type_text_sync(selector, text)
    obs.trace_sync("browser:type", {"selector": selector, "text": text}, result)
    return result


@tool
def run_js(expression: str) -> str:
    """Run JavaScript in the browser and return the result.

    Args:
        expression: JavaScript expression to evaluate.
    """
    result = browser.evaluate_sync(expression)
    obs.trace_sync("browser:evaluate", {"expression": expression[:200]}, result[:500])
    return result


@tool
def take_screenshot() -> str:
    """Take a screenshot of the current browser page.

    Returns a description of the screenshot (base64 data is available
    through the API for programmatic use).
    """
    result = browser.screenshot_sync()
    obs.trace_sync("browser:screenshot", {}, result)
    return result


# ── Agent ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a capable assistant with access to a sandboxed code interpreter \
and a cloud browser.

**Code Interpreter:**
- `run_python` — execute Python code. State persists across calls.
- `run_shell` — run shell commands in the sandbox.
- `install_package` — install pip packages.

Use the code interpreter for calculations, data analysis, file processing, \
web scraping with requests, or any task that benefits from running code.

**Browser:**
- `open_browser` — start a cloud browser session.
- `browse_to` — navigate to a URL.
- `read_page` — read the text content of the current page.
- `click_element` — click a button, link, or other element (CSS selector).
- `type_into` — type text into an input field (CSS selector).
- `run_js` — run JavaScript on the page.
- `take_screenshot` — capture a screenshot.
- `close_browser` — stop the session when done.

When using the browser, always start with `open_browser`, then `browse_to` \
a URL. Use `read_page` to see what's on the page. Use `click_element` and \
`type_into` with CSS selectors to interact with elements. Use `run_js` for \
advanced DOM manipulation.

When asked to do something, prefer the code interpreter for data tasks. \
Use the browser when you need to interact with a website visually.
"""


def main():
    model = BedrockModel(
        model_id="us.anthropic.claude-sonnet-4-20250514-v1:0",
        streaming=True,
    )

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[
            run_python,
            run_shell,
            install_package,
            open_browser,
            close_browser,
            browse_to,
            read_page,
            click_element,
            type_into,
            run_js,
            take_screenshot,
        ],
    )

    print("Code + Browser agent ready.")
    print("Connected to platform at http://localhost:8000")
    print("Type 'quit' to exit.\n")

    obs.log_sync("info", "Code+Browser agent started")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        response = agent(user_input)

        obs.trace_sync(
            "conversation:turn",
            {"user": user_input},
            str(response),
            tags=["conversation"],
        )
        print()

    # Clean up sessions
    code.close_sync()
    browser.close_sync()
    obs.log_sync("info", "Code+Browser agent stopped")


if __name__ == "__main__":
    main()
