"""Intent-level test: credential contextvars don't leak between concurrent requests.

Every primitive in the gateway reads credentials from request-scoped
``contextvars.ContextVar``.  The multi-tenant contract — stated in
``CLAUDE.md``'s Security Principles — is:

    Request-scoped credential isolation.  AWS credentials, service
    credentials, provider routing, and authenticated principals are
    stored in Python contextvars.  No shared mutable state between
    requests, even under concurrent load.

Existing tests set contextvars *serially* and read them back — that
only proves the set/get round-trips.  A regression where someone
replaced a ``ContextVar`` with a module-level global would still pass
every existing test.  This file drives actual concurrency:
``asyncio.gather`` of many tasks, each in its own ``contextvars.Context``,
each setting different credential values, each yielding back to the
scheduler between set and read to force interleaving.  If any value
leaks across tasks, the assertion fails.
"""

from __future__ import annotations

import asyncio

import pytest

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import (
    AWSCredentials,
    get_authenticated_principal,
    get_aws_credentials,
    get_provider_override,
    get_service_credentials,
    set_authenticated_principal,
    set_aws_credentials,
    set_provider_overrides,
    set_service_credentials,
)

# ``asyncio.create_task`` captures the current ``contextvars.Context``
# at creation time, giving each task its own copy — matching what
# FastAPI does per HTTP request.  If any of the module-level
# credential holders were ever converted to plain globals, concurrent
# tasks would all observe the same value (last writer wins after the
# barrier releases) and these tests would fail.  A barrier pattern
# (install → ``ready.set()`` → ``go.wait()`` → read) forces the
# interleaving so the leak is observable.


class TestAWSCredentialIsolation:
    @pytest.mark.asyncio
    async def test_four_concurrent_tasks_see_only_their_own_aws_creds(self):
        """Four concurrent requests with four different AWS creds →
        each sees exactly its own, never a peer's.

        The important bit is the ``asyncio.Event`` barrier: every
        task installs its creds *before* any task is allowed to read.
        A global-variable implementation would fail this because the
        last set() before the barrier release wins for everyone.
        """
        creds_list = [
            AWSCredentials(access_key_id=f"AK{i}", secret_access_key=f"SK{i}", region=f"us-east-{i}") for i in range(4)
        ]
        go = asyncio.Event()
        ready = [asyncio.Event() for _ in creds_list]

        async def task(i: int) -> tuple[str, AWSCredentials | None]:
            set_aws_credentials(creds_list[i])
            ready[i].set()
            await go.wait()
            for _ in range(5):
                await asyncio.sleep(0)
            return f"AK{i}", get_aws_credentials()

        tasks = [asyncio.create_task(task(i)) for i in range(4)]
        await asyncio.gather(*(e.wait() for e in ready))
        go.set()
        results = await asyncio.gather(*tasks)

        for expected_id, creds in results:
            assert creds is not None, f"task {expected_id} lost its creds"
            assert creds.access_key_id == expected_id, (
                f"task {expected_id} saw peer creds {creds.access_key_id} — contextvar leak"
            )

    @pytest.mark.asyncio
    async def test_aws_creds_cleared_in_one_task_does_not_affect_peers(self):
        """One task sets creds, another clears to None, they must not collide."""
        alice_creds = AWSCredentials(access_key_id="alice-ak", secret_access_key="alice-sk")
        go = asyncio.Event()
        a_ready, b_ready = asyncio.Event(), asyncio.Event()

        async def alice():
            set_aws_credentials(alice_creds)
            a_ready.set()
            await go.wait()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return get_aws_credentials()

        async def bob():
            set_aws_credentials(None)
            b_ready.set()
            await go.wait()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return get_aws_credentials()

        alice_task = asyncio.create_task(alice())
        bob_task = asyncio.create_task(bob())
        await asyncio.gather(a_ready.wait(), b_ready.wait())
        go.set()
        alice_result, bob_result = await asyncio.gather(alice_task, bob_task)

        assert alice_result is not None
        assert alice_result.access_key_id == "alice-ak"
        assert bob_result is None  # Bob explicitly cleared and must see that


class TestPrincipalIsolation:
    @pytest.mark.asyncio
    async def test_two_concurrent_tasks_see_their_own_principal(self):
        alice = AuthenticatedPrincipal(id="alice", type="user")
        bob = AuthenticatedPrincipal(id="bob", type="user")
        go = asyncio.Event()
        a_ready, b_ready = asyncio.Event(), asyncio.Event()

        async def alice_task():
            set_authenticated_principal(alice)
            a_ready.set()
            await go.wait()
            for _ in range(5):
                await asyncio.sleep(0)
            return get_authenticated_principal()

        async def bob_task():
            set_authenticated_principal(bob)
            b_ready.set()
            await go.wait()
            for _ in range(5):
                await asyncio.sleep(0)
            return get_authenticated_principal()

        at = asyncio.create_task(alice_task())
        bt = asyncio.create_task(bob_task())
        await asyncio.gather(a_ready.wait(), b_ready.wait())
        go.set()
        ar, br = await asyncio.gather(at, bt)

        assert ar is not None and ar.id == "alice"
        assert br is not None and br.id == "bob"

    @pytest.mark.asyncio
    async def test_many_concurrent_principals_never_crosstalk(self):
        """50 concurrent tasks each with a unique principal id — each
        must see exactly its own after the barrier releases.
        """
        n = 50
        go = asyncio.Event()
        ready = [asyncio.Event() for _ in range(n)]

        async def task(i: int):
            p = AuthenticatedPrincipal(id=f"user-{i}", type="user")
            set_authenticated_principal(p)
            ready[i].set()
            await go.wait()
            for _ in range(3):
                await asyncio.sleep(0)
            seen = get_authenticated_principal()
            return i, seen.id if seen else None

        tasks = [asyncio.create_task(task(i)) for i in range(n)]
        await asyncio.gather(*(e.wait() for e in ready))
        go.set()
        results = await asyncio.gather(*tasks)

        for expected_i, seen in results:
            assert seen == f"user-{expected_i}", f"task {expected_i} saw {seen} — leak"


class TestServiceCredentialIsolation:
    @pytest.mark.asyncio
    async def test_concurrent_langfuse_creds_isolated(self):
        alice_creds = {"langfuse": {"public_key": "pk-alice", "secret_key": "sk-alice"}}
        bob_creds = {"langfuse": {"public_key": "pk-bob", "secret_key": "sk-bob"}}
        go = asyncio.Event()
        a_ready, b_ready = asyncio.Event(), asyncio.Event()

        async def alice_task():
            set_service_credentials(alice_creds)
            a_ready.set()
            await go.wait()
            for _ in range(5):
                await asyncio.sleep(0)
            return get_service_credentials("langfuse")

        async def bob_task():
            set_service_credentials(bob_creds)
            b_ready.set()
            await go.wait()
            for _ in range(5):
                await asyncio.sleep(0)
            return get_service_credentials("langfuse")

        at = asyncio.create_task(alice_task())
        bt = asyncio.create_task(bob_task())
        await asyncio.gather(a_ready.wait(), b_ready.wait())
        go.set()
        ar, br = await asyncio.gather(at, bt)

        assert ar == {"public_key": "pk-alice", "secret_key": "sk-alice"}
        assert br == {"public_key": "pk-bob", "secret_key": "sk-bob"}


class TestProviderOverrideIsolation:
    @pytest.mark.asyncio
    async def test_concurrent_provider_overrides_dont_bleed(self):
        go = asyncio.Event()
        a_ready, b_ready = asyncio.Event(), asyncio.Event()

        async def alice_task():
            set_provider_overrides({"memory": "mem0"})
            a_ready.set()
            await go.wait()
            for _ in range(5):
                await asyncio.sleep(0)
            return get_provider_override("memory")

        async def bob_task():
            set_provider_overrides({"memory": "agentcore"})
            b_ready.set()
            await go.wait()
            for _ in range(5):
                await asyncio.sleep(0)
            return get_provider_override("memory")

        at = asyncio.create_task(alice_task())
        bt = asyncio.create_task(bob_task())
        await asyncio.gather(a_ready.wait(), b_ready.wait())
        go.set()
        ar, br = await asyncio.gather(at, bt)

        assert ar == "mem0"
        assert br == "agentcore"


class TestMiddlewareE2EConcurrency:
    """End-to-end: concurrent HTTP requests with different credential
    headers must land in separate request contexts.

    Uses the real ``RequestContextMiddleware`` + a test endpoint that
    echoes back what it saw.  Guards against a regression that moves
    credential extraction above the middleware (into a place where
    FastAPI doesn't start a fresh Context per request).
    """

    @pytest.mark.asyncio
    async def test_two_concurrent_requests_see_their_own_aws_headers(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        from agentic_primitives_gateway.middleware import RequestContextMiddleware

        echo_app = FastAPI()
        echo_app.add_middleware(RequestContextMiddleware)

        @echo_app.get("/echo")
        async def echo():
            # Small yield so concurrent requests interleave inside the handler.
            await asyncio.sleep(0.01)
            creds = get_aws_credentials()
            return {
                "access_key": creds.access_key_id if creds else None,
                "region": creds.region if creds else None,
            }

        transport = ASGITransport(app=echo_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alice_headers = {
                "x-aws-access-key-id": "AKIA-ALICE",
                "x-aws-secret-access-key": "SECRET-ALICE",
                "x-aws-region": "us-east-1",
            }
            bob_headers = {
                "x-aws-access-key-id": "AKIA-BOB",
                "x-aws-secret-access-key": "SECRET-BOB",
                "x-aws-region": "eu-west-2",
            }
            # Fire many interleaved requests; each must see its own creds.
            resps = await asyncio.gather(
                *[client.get("/echo", headers=alice_headers) for _ in range(5)],
                *[client.get("/echo", headers=bob_headers) for _ in range(5)],
            )

        alice_results = [r.json() for r in resps[:5]]
        bob_results = [r.json() for r in resps[5:]]

        for r in alice_results:
            assert r == {"access_key": "AKIA-ALICE", "region": "us-east-1"}
        for r in bob_results:
            assert r == {"access_key": "AKIA-BOB", "region": "eu-west-2"}
