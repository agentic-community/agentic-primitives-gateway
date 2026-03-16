"""Shared helpers for E2E example scripts.

Provides Cedar policy enforcement demo and cleanup functions.
Both sync and async variants are provided.

The demo uses the gateway's scoped enforcement engine (auto-provisioned at
startup). It clears existing policies to show default-deny, then creates a
permit-all policy to unblock access.
"""

from __future__ import annotations

import asyncio
import time

import httpx

# ── Cedar Policy Enforcement Demo ────────────────────────────────────

POLICY_REFRESH_WAIT = 7  # seconds; E2E configs use policy_refresh_interval: 5


def _auth_headers(auth_token: str | None) -> dict[str, str]:
    if auth_token:
        return {"Authorization": f"Bearer {auth_token}"}
    return {}


def _wait_for_refresh_sync() -> None:
    print(f"  Waiting {POLICY_REFRESH_WAIT}s for policy refresh...", end="", flush=True)
    for _ in range(POLICY_REFRESH_WAIT):
        time.sleep(1)
        print(".", end="", flush=True)
    print(" done!")


async def _wait_for_refresh_async() -> None:
    print(f"  Waiting {POLICY_REFRESH_WAIT}s for policy refresh...", end="", flush=True)
    for _ in range(POLICY_REFRESH_WAIT):
        await asyncio.sleep(1)
        print(".", end="", flush=True)
    print(" done!")


def cedar_demo_sync(
    gateway_url: str,
    namespace: str,
    auth_token: str | None = None,
    **_kwargs: str,
) -> tuple[str, str]:
    """Synchronous Cedar enforcement demo: default-deny -> permit -> verify.

    Returns (engine_id, policy_id) for cleanup.
    """
    headers = _auth_headers(auth_token)

    print("-" * 60)
    print("  Cedar Policy Enforcement Demo")
    print("-" * 60)

    # Step 0: Get the enforcer's engine ID
    print("\n  [0] Discovering enforcement engine...")
    resp = httpx.get(f"{gateway_url}/api/v1/policy/enforcement", headers=headers, timeout=30.0)
    if resp.status_code != 200 or not resp.json().get("active"):
        print("      Cedar enforcement is not active, skipping demo")
        print("-" * 60)
        return ("", "")

    engine_id = resp.json().get("engine_id", "")
    if not engine_id:
        print("      No enforcement engine configured, skipping demo")
        print("-" * 60)
        return ("", "")
    print(f"      Engine: {engine_id}")

    # Step 1: Clear existing policies to demonstrate default-deny
    print("\n  [1] Clearing policies to demonstrate default-deny...")
    resp = httpx.get(
        f"{gateway_url}/api/v1/policy/engines/{engine_id}/policies",
        headers=headers,
        timeout=30.0,
    )
    cleared = False
    if resp.status_code == 200:
        for pol in resp.json().get("policies", []):
            pid = pol.get("policy_id", "")
            httpx.delete(
                f"{gateway_url}/api/v1/policy/engines/{engine_id}/policies/{pid}",
                headers=headers,
                timeout=30.0,
            )
            cleared = True
    if cleared:
        print("      Cleared existing policies")
        _wait_for_refresh_sync()

    # Step 2: Try to store a memory -- should be blocked (default-deny)
    print("\n  [2] Attempting to store a memory (no policies loaded)...")
    resp = httpx.post(
        f"{gateway_url}/api/v1/memory/{namespace}",
        json={"key": "cedar-test", "content": "hello"},
        headers=headers,
        timeout=30.0,
    )
    if resp.status_code == 403:
        print(f"      BLOCKED (HTTP {resp.status_code})")
        print()
        print("      This is expected! Cedar enforcement uses default-deny.")
        print("      No policies are loaded, so all requests are rejected.")
        print()
        input("      Press Enter to create a permit-all policy and unblock access...")
    else:
        print(f"      HTTP {resp.status_code} -- expected 403, skipping demo")
        print("-" * 60)
        return (engine_id, "")

    # Step 3: Create a permit-all policy in the enforcer's engine
    print("\n  [3] Creating permit-all policy...")
    resp = httpx.post(
        f"{gateway_url}/api/v1/policy/engines/{engine_id}/policies",
        json={
            "policy_body": "permit(principal, action, resource is AgentCore::Gateway);",
            "description": "E2E demo: permit all",
        },
        headers=headers,
        timeout=30.0,
    )
    if resp.status_code != 201:
        print(f"      Failed to create policy (HTTP {resp.status_code})")
        print("-" * 60)
        return (engine_id, "")
    policy_id = resp.json()["policy_id"]
    print(f"      Policy created: {policy_id}")

    # Step 4: Wait for the enforcer to pick up the new policy
    print()
    _wait_for_refresh_sync()

    # Step 5: Retry -- should now succeed
    print("\n  [4] Retrying memory store (policy should be active)...")
    resp = httpx.post(
        f"{gateway_url}/api/v1/memory/{namespace}",
        json={"key": "cedar-test", "content": "Cedar enforcement works!"},
        headers=headers,
        timeout=30.0,
    )
    if resp.status_code in (200, 201):
        print(f"      ALLOWED (HTTP {resp.status_code}) -- permit-all policy is active!")
    else:
        print(f"      HTTP {resp.status_code} -- policy may not have refreshed yet")

    # Clean up the test memory
    httpx.delete(
        f"{gateway_url}/api/v1/memory/{namespace}/cedar-test",
        headers=headers,
        timeout=30.0,
    )

    print("\n  Cedar enforcement demo complete. Chat loop starting...\n")
    print("-" * 60)
    return (engine_id, policy_id)


def cedar_cleanup_sync(
    gateway_url: str,
    engine_id: str,
    policy_id: str,
    auth_token: str | None = None,
) -> None:
    """Remove the demo policy (keeps the engine for future use)."""
    if not policy_id:
        return

    headers = _auth_headers(auth_token)
    print("\n  Cleaning up Cedar demo policy...")
    resp = httpx.delete(
        f"{gateway_url}/api/v1/policy/engines/{engine_id}/policies/{policy_id}",
        headers=headers,
        timeout=30.0,
    )
    print(f"    Deleted policy: HTTP {resp.status_code}")


async def cedar_demo_async(
    gateway_url: str,
    namespace: str,
    auth_token: str | None = None,
    **_kwargs: str,
) -> tuple[str, str]:
    """Async Cedar enforcement demo: default-deny -> permit -> verify.

    Returns (engine_id, policy_id) for cleanup.
    """
    headers = _auth_headers(auth_token)

    print("-" * 60)
    print("  Cedar Policy Enforcement Demo")
    print("-" * 60)

    async with httpx.AsyncClient(base_url=gateway_url, timeout=60.0) as client:
        # Step 0: Get the enforcer's engine ID
        print("\n  [0] Discovering enforcement engine...")
        resp = await client.get("/api/v1/policy/enforcement", headers=headers)
        if resp.status_code != 200 or not resp.json().get("active"):
            print("      Cedar enforcement is not active, skipping demo")
            print("-" * 60)
            return ("", "")

        engine_id = resp.json().get("engine_id", "")
        if not engine_id:
            print("      No enforcement engine configured, skipping demo")
            print("-" * 60)
            return ("", "")
        print(f"      Engine: {engine_id}")

        # Step 1: Clear existing policies to demonstrate default-deny
        print("\n  [1] Clearing policies to demonstrate default-deny...")
        resp = await client.get(f"/api/v1/policy/engines/{engine_id}/policies", headers=headers)
        cleared = False
        if resp.status_code == 200:
            for pol in resp.json().get("policies", []):
                pid = pol.get("policy_id", "")
                await client.delete(
                    f"/api/v1/policy/engines/{engine_id}/policies/{pid}",
                    headers=headers,
                )
                cleared = True
        if cleared:
            print("      Cleared existing policies")
            await _wait_for_refresh_async()

        # Step 2: Try to store a memory -- should be blocked (default-deny)
        print("\n  [2] Attempting to store a memory (no policies loaded)...")
        resp = await client.post(
            f"/api/v1/memory/{namespace}",
            json={"key": "cedar-test", "content": "hello"},
            headers=headers,
        )
        if resp.status_code == 403:
            print(f"      BLOCKED (HTTP {resp.status_code})")
            print()
            print("      This is expected! Cedar enforcement uses default-deny.")
            print("      No policies are loaded, so all requests are rejected.")
            print()
            input("      Press Enter to create a permit-all policy and unblock access...")
        else:
            print(f"      HTTP {resp.status_code} -- expected 403, skipping demo")
            print("-" * 60)
            return (engine_id, "")

        # Step 3: Create a permit-all policy in the enforcer's engine
        print("\n  [3] Creating permit-all policy...")
        resp = await client.post(
            f"/api/v1/policy/engines/{engine_id}/policies",
            json={
                "policy_body": "permit(principal, action, resource is AgentCore::Gateway);",
                "description": "E2E demo: permit all",
            },
            headers=headers,
        )
        if resp.status_code != 201:
            print(f"      Failed to create policy (HTTP {resp.status_code})")
            print("-" * 60)
            return (engine_id, "")
        policy_id = resp.json()["policy_id"]
        print(f"      Policy created: {policy_id}")

        # Step 4: Wait for the enforcer to pick up the new policy
        print()
        await _wait_for_refresh_async()

        # Step 5: Retry -- should now succeed
        print("\n  [4] Retrying memory store (policy should be active)...")
        resp = await client.post(
            f"/api/v1/memory/{namespace}",
            json={"key": "cedar-test", "content": "Cedar enforcement works!"},
            headers=headers,
        )
        if resp.status_code in (200, 201):
            print(f"      ALLOWED (HTTP {resp.status_code}) -- permit-all policy is active!")
        else:
            print(f"      HTTP {resp.status_code} -- policy may not have refreshed yet")

        # Clean up the test memory
        await client.delete(
            f"/api/v1/memory/{namespace}/cedar-test",
            headers=headers,
        )

    print("\n  Cedar enforcement demo complete. Chat loop starting...\n")
    print("-" * 60)
    return (engine_id, policy_id)


async def cedar_cleanup_async(
    gateway_url: str,
    engine_id: str,
    policy_id: str,
    auth_token: str | None = None,
) -> None:
    """Remove the demo policy (keeps the engine for future use)."""
    if not policy_id:
        return

    headers = _auth_headers(auth_token)
    print("\n  Cleaning up Cedar demo policy...")
    async with httpx.AsyncClient(base_url=gateway_url, timeout=60.0) as client:
        resp = await client.delete(
            f"/api/v1/policy/engines/{engine_id}/policies/{policy_id}",
            headers=headers,
        )
        print(f"    Deleted policy: HTTP {resp.status_code}")
