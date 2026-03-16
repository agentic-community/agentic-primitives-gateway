"""A2A protocol client example — interact with APG agents via the A2A protocol.

Demonstrates:
  1. JWT authentication via Keycloak (same pattern as strands-agentcore)
  2. Agent discovery via the gateway-level agent card
  3. Per-agent card discovery
  4. Synchronous message send (non-streaming)
  5. Streaming message send with real-time SSE events
  6. Task status polling
  7. Task cancellation

Prerequisites:
    pip install httpx

Usage:
    # Start the gateway with JWT auth enabled
    AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=configs/local-jwt.yaml \
      uvicorn agentic_primitives_gateway.main:app --port 8000

    # Set Keycloak env vars (or use defaults below)
    export KEYCLOAK_ISSUER=https://keycloak.example.com/realms/my-realm
    export KEYCLOAK_CLIENT_ID=agentic-gateway
    export KEYCLOAK_USERNAME=admin
    export KEYCLOAK_PASSWORD=secret

    # Run this example
    python a2a_example.py

    # Or without auth (noop mode):
    AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=configs/local.yaml \
      uvicorn agentic_primitives_gateway.main:app --port 8000
    python a2a_example.py
"""

from __future__ import annotations

import json
import os
import sys

import httpx

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")


# ── Keycloak JWT authentication ──────────────────────────────────────


def get_keycloak_token(
    issuer: str,
    client_id: str,
    username: str,
    password: str,
) -> str:
    """Obtain a JWT access token from Keycloak using the Resource Owner Password Grant.

    Requires "Direct access grants" enabled on the Keycloak client.
    """
    token_url = f"{issuer}/protocol/openid-connect/token"
    resp = httpx.post(
        token_url,
        data={
            "grant_type": "password",
            "client_id": client_id,
            "username": username,
            "password": password,
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_auth_headers() -> dict[str, str]:
    """Build auth headers from Keycloak env vars. Returns empty dict if not configured."""
    issuer = os.environ.get("KEYCLOAK_ISSUER")
    username = os.environ.get("KEYCLOAK_USERNAME")
    password = os.environ.get("KEYCLOAK_PASSWORD")

    if not (issuer and username and password):
        print("No KEYCLOAK_* env vars set — running without authentication (noop mode)")
        return {}

    client_id = os.environ.get("KEYCLOAK_CLIENT_ID", "agentic-gateway")
    token = get_keycloak_token(issuer, client_id, username, password)
    print(f"Authenticated as {username} via Keycloak ({issuer})")
    return {"Authorization": f"Bearer {token}"}


def print_header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def discover_agents(headers: dict[str, str]) -> list[dict]:
    """Step 1: Discover available agents via the gateway-level agent card."""
    print_header("1. Discover Agents (Gateway Card)")

    # Agent card is auth-exempt, but we pass headers for consistency
    resp = httpx.get(f"{GATEWAY_URL}/.well-known/agent.json", headers=headers)
    resp.raise_for_status()
    card = resp.json()

    print(f"Gateway: {card['name']}")
    print(f"Description: {card['description']}")
    print(f"Capabilities: streaming={card['capabilities'].get('streaming', False)}")
    print(f"\nAvailable agents ({len(card['skills'])} skills):")
    for skill in card["skills"]:
        print(f"  - {skill['name']}: {skill['description']}")
        if skill.get("tags"):
            print(f"    tags: {', '.join(skill['tags'])}")

    return card["skills"]


def discover_agent(agent_name: str, headers: dict[str, str]) -> dict:
    """Step 2: Get a per-agent card with its specific endpoint."""
    print_header(f"2. Per-Agent Card: {agent_name}")

    # Per-agent cards are auth-exempt for public agents
    resp = httpx.get(f"{GATEWAY_URL}/a2a/agents/{agent_name}/.well-known/agent.json", headers=headers)
    resp.raise_for_status()
    card = resp.json()

    print(f"Agent: {card['name']}")
    print(f"Description: {card['description']}")
    print(f"Endpoint: {card['supported_interfaces'][0]['url']}")
    print(f"Protocol: {card['supported_interfaces'][0]['protocol_binding']}")

    if card.get("security_schemes"):
        print(f"Auth: {list(card['security_schemes'].keys())}")
    else:
        print("Auth: none (noop mode)")

    return card


def send_message_sync(agent_name: str, text: str, headers: dict[str, str]) -> dict:
    """Step 3: Send a synchronous message and get a completed task back."""
    print_header(f"3. Sync Message to '{agent_name}'")
    print(f"Message: {text}\n")

    import uuid

    request_body = {
        "message": {
            "message_id": uuid.uuid4().hex[:12],
            "role": "user",
            "parts": [{"text": text}],
        }
    }

    resp = httpx.post(
        f"{GATEWAY_URL}/a2a/agents/{agent_name}/message:send",
        json=request_body,
        headers=headers,
        timeout=120.0,
    )
    resp.raise_for_status()
    task = resp.json()

    print(f"Task ID: {task['id']}")
    print(f"Status: {task['status']['state']}")

    if task.get("artifacts"):
        for artifact in task["artifacts"]:
            for part in artifact.get("parts", []):
                if part.get("text"):
                    print(f"\nResponse:\n{part['text'][:500]}")
                    if len(part["text"]) > 500:
                        print(f"  ... ({len(part['text'])} chars total)")

    if task.get("metadata"):
        print(f"\nMetadata: {json.dumps(task['metadata'], indent=2)}")

    return task


def send_message_streaming(agent_name: str, text: str, headers: dict[str, str]) -> str | None:
    """Step 4: Send a streaming message and process A2A SSE events in real-time."""
    print_header(f"4. Streaming Message to '{agent_name}'")
    print(f"Message: {text}\n")

    import uuid

    request_body = {
        "message": {
            "message_id": uuid.uuid4().hex[:12],
            "role": "user",
            "parts": [{"text": text}],
        }
    }

    task_id = None
    token_count = 0

    with httpx.stream(
        "POST",
        f"{GATEWAY_URL}/a2a/agents/{agent_name}/message:stream",
        json=request_body,
        headers=headers,
        timeout=120.0,
    ) as resp:
        resp.raise_for_status()
        buffer = ""

        print("Streaming response: ", end="", flush=True)
        for chunk in resp.iter_text():
            buffer += chunk
            while "\n\n" in buffer:
                event_str, buffer = buffer.split("\n\n", 1)
                for line in event_str.split("\n"):
                    if line.startswith("data: "):
                        data = json.loads(line[6:])
                        event_type = data.get("type", "")

                        if event_type == "status_update":
                            state = data.get("status", {}).get("state", "")
                            if state == "working":
                                print("[working] ", end="", flush=True)

                        elif event_type == "message":
                            parts = data.get("parts", [])
                            for part in parts:
                                if part.get("text"):
                                    print(part["text"], end="", flush=True)
                                    token_count += 1
                                elif part.get("data"):
                                    tool = part["data"].get("tool_name", "")
                                    if tool:
                                        print(f"\n  [tool: {tool}]", end="", flush=True)

                        elif event_type == "artifact_update":
                            # Final artifact — we already printed tokens
                            pass

                        elif event_type == "task":
                            task_id = data.get("id")
                            state = data.get("status", {}).get("state", "")
                            print(f"\n\n[Task {task_id}: {state}]")

    print(f"\n({token_count} token events received)")
    return task_id


def get_task_status(agent_name: str, task_id: str, headers: dict[str, str]) -> dict:
    """Step 5: Poll task status."""
    print_header(f"5. Get Task Status: {task_id}")

    resp = httpx.get(f"{GATEWAY_URL}/a2a/agents/{agent_name}/tasks/{task_id}", headers=headers)
    resp.raise_for_status()
    task = resp.json()

    print(f"Task ID: {task['id']}")
    print(f"Status: {task['status']['state']}")

    if task.get("artifacts"):
        print(f"Artifacts: {len(task['artifacts'])}")
        for artifact in task["artifacts"]:
            for part in artifact.get("parts", []):
                if part.get("text"):
                    print(f"  Response preview: {part['text'][:200]}...")

    return task


def cancel_task(agent_name: str, task_id: str, headers: dict[str, str]) -> dict:
    """Step 6: Cancel a task (demonstrates the cancel endpoint)."""
    print_header(f"6. Cancel Task: {task_id}")

    resp = httpx.post(f"{GATEWAY_URL}/a2a/agents/{agent_name}/tasks/{task_id}:cancel", headers=headers)
    resp.raise_for_status()
    task = resp.json()

    print(f"Task ID: {task['id']}")
    print(f"Status: {task['status']['state']}")

    return task


def main() -> None:
    """Run the full A2A protocol example."""
    print("\nA2A Protocol Example — Agentic Primitives Gateway")
    print("=" * 60)

    # Check gateway is running
    try:
        httpx.get(f"{GATEWAY_URL}/healthz").raise_for_status()
    except httpx.ConnectError:
        print(f"\nError: Gateway not running at {GATEWAY_URL}")
        print("Start it with: ./run.sh local")
        sys.exit(1)

    # Authenticate via Keycloak (if env vars are set)
    headers = get_auth_headers()

    # 1. Discover all agents
    skills = discover_agents(headers)
    if not skills:
        print("\nNo public agents found. Make sure agents are configured with shared_with: ['*']")
        sys.exit(1)

    # Pick the first available agent
    agent_name = skills[0]["id"]
    print(f"\nUsing agent: {agent_name}")

    # 2. Get per-agent card
    discover_agent(agent_name, headers)

    # 3. Sync message
    send_message_sync(agent_name, "What can you help me with? Reply briefly.", headers)

    # 4. Streaming message
    task_id = send_message_streaming(agent_name, "Explain what A2A protocol is in 2 sentences.", headers)

    # 5. Check task status (using the task from streaming)
    if task_id:
        get_task_status(agent_name, task_id, headers)

    # 6. Cancel demonstration (on an already-completed task — shows the flow)
    if task_id:
        cancel_task(agent_name, task_id, headers)

    print_header("Done!")
    print("The A2A protocol enables any external agent to discover and")
    print("interact with APG agents using a standard protocol.\n")
    print("Key URLs:")
    print(f"  Gateway card:    {GATEWAY_URL}/.well-known/agent.json")
    print(f"  Per-agent card:  {GATEWAY_URL}/a2a/agents/{agent_name}/.well-known/agent.json")
    print(f"  Send message:    POST {GATEWAY_URL}/a2a/agents/{agent_name}/message:send")
    print(f"  Stream message:  POST {GATEWAY_URL}/a2a/agents/{agent_name}/message:stream")
    print()


if __name__ == "__main__":
    main()
