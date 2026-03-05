#!/usr/bin/env python3
"""Policy enforcement demo.

Demonstrates the full Cedar enforcement lifecycle with fine-grained
action-level and principal-scoped access control:

  Part 1 -- Default-deny and broad permit
    1. All requests blocked (no policies)
    2. Create engine + wildcard permit for one agent
    3. Allowed agent can do everything; denied agent is blocked

  Part 2 -- Fine-grained action-level policies
    4. Replace the wildcard permit with read-only access
    5. Allowed agent can read memory but NOT write
    6. Add a second policy for a "writer" agent with write-only access
    7. Writer can store but NOT read; reader can read but NOT store

  Part 3 -- Forbid overrides permit
    8. Add a broad permit for the denied agent, then forbid a specific action
    9. Denied agent can read but NOT delete (forbid takes precedence)

  Part 4 -- Cleanup

Prerequisites:
    pip install -e ".[cedar,dev]"

    # Start the gateway with kitchen-sink config (Cedar enforcement enabled):
    AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=configs/kitchen-sink.yaml \\
        uvicorn agentic_primitives_gateway.main:app --reload

Usage:
    python examples/enforcement_demo.py
    python examples/enforcement_demo.py --base-url http://localhost:8000
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import httpx

BASE_URL = "http://localhost:8000"
READER_AGENT = "reader-agent"
WRITER_AGENT = "writer-agent"
BLOCKED_AGENT = "untrusted-agent"
NAMESPACE = "enforcement-demo"

# Refresh interval in kitchen-sink.yaml is 5s; wait a bit longer
REFRESH_WAIT = 7


def header(msg: str) -> None:
    print(f"\n{'=' * 64}")
    print(f"  {msg}")
    print(f"{'=' * 64}\n")


def result(label: str, status: int, body: str) -> None:
    icon = "+" if status < 400 else "x"
    print(f"  [{icon}] {label}: HTTP {status}")
    if body:
        preview = body[:200] + ("..." if len(body) > 200 else "")
        print(f"      {preview}")


def expect(resp: httpx.Response, code: int) -> None:
    assert resp.status_code == code, f"Expected {code}, got {resp.status_code}: {resp.text}"


def wait_for_refresh() -> None:
    print(f"  Waiting {REFRESH_WAIT}s for background policy refresh...")
    for i in range(REFRESH_WAIT):
        time.sleep(1)
        print(f"  {'.' * (i + 1)}", end="\r")
    print(f"  {'.' * REFRESH_WAIT} done!")


async def create_policy(
    client: httpx.AsyncClient,
    engine_id: str,
    cedar: str,
    description: str,
) -> str:
    """Create a policy and return its ID."""
    print(f"  Cedar: {cedar}")
    resp = await client.post(
        f"/api/v1/policy/engines/{engine_id}/policies",
        json={"policy_body": cedar, "description": description},
    )
    result(f"Create policy ({description})", resp.status_code, "")
    expect(resp, 201)
    policy_id: str = resp.json()["policy_id"]
    print(f"      Policy ID: {policy_id}")
    return policy_id


async def delete_policy(client: httpx.AsyncClient, engine_id: str, policy_id: str) -> None:
    resp = await client.delete(f"/api/v1/policy/engines/{engine_id}/policies/{policy_id}")
    result(f"Delete policy {policy_id[:12]}...", resp.status_code, "")


async def main(base_url: str) -> None:
    policy_ids: list[str] = []
    engine_id: str | None = None

    async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as client:
        # ==============================================================
        header("Step 0: Verify gateway is running")
        # ==============================================================
        try:
            resp = await client.get("/healthz")
            result("Health check", resp.status_code, resp.text)
        except httpx.ConnectError:
            print("  [!] Cannot connect to gateway. Is it running?")
            print(f"      Expected at: {base_url}")
            sys.exit(1)

        # ==============================================================
        header("Step 1: Default-deny -- all requests blocked")
        # ==============================================================
        resp = await client.post(
            f"/api/v1/memory/{NAMESPACE}",
            json={"key": "test", "content": "hello"},
            headers={"X-Agent-Id": READER_AGENT},
        )
        result(f"Store memory as {READER_AGENT}", resp.status_code, resp.text)
        if resp.status_code == 201:
            print("\n  [!] Request was ALLOWED -- enforcement may not be active.")
            print("      Make sure you're using a config with CedarPolicyEnforcer.")
            print("      Try: AGENTIC_PRIMITIVES_GATEWAY_CONFIG_FILE=configs/kitchen-sink.yaml")
            sys.exit(1)
        expect(resp, 403)

        # ==============================================================
        header("Step 2: Create policy engine")
        # ==============================================================
        resp = await client.post(
            "/api/v1/policy/engines",
            json={"name": "enforcement-demo", "description": "Demo engine"},
        )
        result("Create policy engine", resp.status_code, "")
        expect(resp, 201)
        engine_id = resp.json()["policy_engine_id"]
        print(f"      Engine ID: {engine_id}")

        # ==============================================================
        header("Step 3: Broad permit -- reader-agent gets full access")
        # ==============================================================
        pid = await create_policy(
            client,
            engine_id,
            f'permit(principal == Agent::"{READER_AGENT}", action, resource);',
            "reader-agent: full access",
        )
        policy_ids.append(pid)

        wait_for_refresh()

        # Reader can store and read
        resp = await client.post(
            f"/api/v1/memory/{NAMESPACE}",
            json={"key": "k1", "content": "broad permit works"},
            headers={"X-Agent-Id": READER_AGENT},
        )
        result(f"Store memory as {READER_AGENT}", resp.status_code, "")
        expect(resp, 201)

        resp = await client.get(
            f"/api/v1/memory/{NAMESPACE}/k1",
            headers={"X-Agent-Id": READER_AGENT},
        )
        result(f"Read memory as {READER_AGENT}", resp.status_code, resp.text)
        expect(resp, 200)

        # Blocked agent still denied
        resp = await client.get(
            f"/api/v1/memory/{NAMESPACE}/k1",
            headers={"X-Agent-Id": BLOCKED_AGENT},
        )
        result(f"Read memory as {BLOCKED_AGENT}", resp.status_code, resp.text)
        expect(resp, 403)

        # ==============================================================
        header("Step 4: Replace with read-only policy")
        # ==============================================================
        print("  Removing broad permit, adding read-only...\n")
        await delete_policy(client, engine_id, policy_ids.pop())

        pid = await create_policy(
            client,
            engine_id,
            (
                f"permit(\n"
                f'  principal == Agent::"{READER_AGENT}",\n'
                f'  action == Action::"memory:retrieve_memory",\n'
                f"  resource\n"
                f");"
            ),
            "reader-agent: read-only memory",
        )
        policy_ids.append(pid)

        # Also allow list so we can test both
        pid = await create_policy(
            client,
            engine_id,
            (
                f"permit(\n"
                f'  principal == Agent::"{READER_AGENT}",\n'
                f'  action == Action::"memory:list_memories",\n'
                f"  resource\n"
                f");"
            ),
            "reader-agent: list memory",
        )
        policy_ids.append(pid)

        wait_for_refresh()

        # ==============================================================
        header("Step 5: Reader can read but NOT write")
        # ==============================================================
        resp = await client.get(
            f"/api/v1/memory/{NAMESPACE}/k1",
            headers={"X-Agent-Id": READER_AGENT},
        )
        result(f"Read memory as {READER_AGENT}", resp.status_code, resp.text)
        expect(resp, 200)

        resp = await client.get(
            f"/api/v1/memory/{NAMESPACE}",
            headers={"X-Agent-Id": READER_AGENT},
        )
        result(f"List memories as {READER_AGENT}", resp.status_code, "")
        expect(resp, 200)

        resp = await client.post(
            f"/api/v1/memory/{NAMESPACE}",
            json={"key": "k2", "content": "should fail"},
            headers={"X-Agent-Id": READER_AGENT},
        )
        result(f"Store memory as {READER_AGENT} (should fail)", resp.status_code, resp.text)
        expect(resp, 403)

        resp = await client.delete(
            f"/api/v1/memory/{NAMESPACE}/k1",
            headers={"X-Agent-Id": READER_AGENT},
        )
        result(f"Delete memory as {READER_AGENT} (should fail)", resp.status_code, resp.text)
        expect(resp, 403)

        # ==============================================================
        header("Step 6: Add writer-agent with write-only access")
        # ==============================================================
        pid = await create_policy(
            client,
            engine_id,
            (
                f"permit(\n"
                f'  principal == Agent::"{WRITER_AGENT}",\n'
                f'  action == Action::"memory:store_memory",\n'
                f"  resource\n"
                f");"
            ),
            "writer-agent: write-only memory",
        )
        policy_ids.append(pid)

        wait_for_refresh()

        # Writer can store
        resp = await client.post(
            f"/api/v1/memory/{NAMESPACE}",
            json={"key": "from-writer", "content": "written by writer-agent"},
            headers={"X-Agent-Id": WRITER_AGENT},
        )
        result(f"Store memory as {WRITER_AGENT}", resp.status_code, "")
        expect(resp, 201)

        # Writer cannot read
        resp = await client.get(
            f"/api/v1/memory/{NAMESPACE}/from-writer",
            headers={"X-Agent-Id": WRITER_AGENT},
        )
        result(f"Read memory as {WRITER_AGENT} (should fail)", resp.status_code, resp.text)
        expect(resp, 403)

        # Reader CAN read what writer stored
        resp = await client.get(
            f"/api/v1/memory/{NAMESPACE}/from-writer",
            headers={"X-Agent-Id": READER_AGENT},
        )
        result(f"Read writer's memory as {READER_AGENT}", resp.status_code, resp.text)
        expect(resp, 200)

        # ==============================================================
        header("Step 7: Forbid overrides permit")
        # ==============================================================
        print("  Give blocked-agent broad access, then forbid delete.\n")

        pid = await create_policy(
            client,
            engine_id,
            f'permit(principal == Agent::"{BLOCKED_AGENT}", action, resource);',
            "blocked-agent: broad permit",
        )
        policy_ids.append(pid)

        pid = await create_policy(
            client,
            engine_id,
            (
                f"forbid(\n"
                f'  principal == Agent::"{BLOCKED_AGENT}",\n'
                f'  action == Action::"memory:delete_memory",\n'
                f"  resource\n"
                f");"
            ),
            "blocked-agent: forbid delete",
        )
        policy_ids.append(pid)

        wait_for_refresh()

        # Blocked agent can read (broad permit)
        resp = await client.get(
            f"/api/v1/memory/{NAMESPACE}/k1",
            headers={"X-Agent-Id": BLOCKED_AGENT},
        )
        result(f"Read memory as {BLOCKED_AGENT}", resp.status_code, resp.text)
        expect(resp, 200)

        # Blocked agent can store (broad permit)
        resp = await client.post(
            f"/api/v1/memory/{NAMESPACE}",
            json={"key": "from-blocked", "content": "I can write"},
            headers={"X-Agent-Id": BLOCKED_AGENT},
        )
        result(f"Store memory as {BLOCKED_AGENT}", resp.status_code, "")
        expect(resp, 201)

        # Blocked agent CANNOT delete (forbid overrides)
        resp = await client.delete(
            f"/api/v1/memory/{NAMESPACE}/from-blocked",
            headers={"X-Agent-Id": BLOCKED_AGENT},
        )
        result(f"Delete memory as {BLOCKED_AGENT} (should fail)", resp.status_code, resp.text)
        expect(resp, 403)

        # ==============================================================
        header("Step 8: Cleanup")
        # ==============================================================
        for pid in policy_ids:
            await delete_policy(client, engine_id, pid)

        resp = await client.delete(f"/api/v1/policy/engines/{engine_id}")
        result("Delete engine", resp.status_code, "")

        # ==============================================================
        header("Done! Fine-grained enforcement demo complete.")
        # ==============================================================
        print("  Summary:")
        print("    1. Default-deny blocked everything with no policies")
        print(f"    2. Broad permit gave {READER_AGENT} full access")
        print(f"    3. Read-only policy: {READER_AGENT} could read, not write")
        print(f"    4. Write-only policy: {WRITER_AGENT} could write, not read")
        print(f"    5. {READER_AGENT} could read what {WRITER_AGENT} stored")
        print(f"    6. Forbid override: {BLOCKED_AGENT} could read/write but NOT delete")
        print("    7. Cleaned up all policies and engine")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Policy enforcement demo")
    parser.add_argument("--base-url", default=BASE_URL, help=f"Gateway URL (default: {BASE_URL})")
    args = parser.parse_args()
    asyncio.run(main(args.base_url))
