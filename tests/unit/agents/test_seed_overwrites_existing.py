"""Intent-level test: YAML seed updates existing system-owned agents.

Contract (CLAUDE.md agents subsystem): "seeded from YAML config on
startup (config overwrites existing agents)".  Meaning: after a
server restart with a changed config, the system-namespace agent
reflects the new YAML — the operator doesn't have to manually
propagate the change through API calls.

Existing ``test_store_file.py`` has seed tests but doesn't verify
the overwrite contract: existing spec + new seed with different
fields → new seed wins.  A regression where seed silently skipped
existing entries would leave the system stuck on the old config —
operators would think they changed something and nothing happened.

Two tests:
- Seed a spec, then re-seed with a different description → the
  new description is on the deployed version.
- Seed a spec, re-seed with the SAME content → no new version
  created (idempotency — seed isn't supposed to churn version
  history for unchanged configs).
"""

from __future__ import annotations

from typing import Any

import pytest

from agentic_primitives_gateway.agents.file_store import FileAgentStore


@pytest.fixture
async def store(tmp_path: Any) -> FileAgentStore:
    return FileAgentStore(path=str(tmp_path / "agents.json"))


class TestSeedOverwritesExisting:
    @pytest.mark.asyncio
    async def test_reseed_with_different_spec_creates_new_version(self, store: FileAgentStore):
        """First seed creates version 1; second seed with a changed
        description creates version 2 and deploys it.  ``get_deployed``
        returns the new description.
        """
        await store.seed_async({"my-agent": {"model": "m", "description": "original"}})
        deployed_v1 = await store.get_deployed("my-agent", "system")
        assert deployed_v1 is not None
        assert deployed_v1.spec.description == "original"
        v1_number = deployed_v1.version_number

        # Re-seed with a different description.
        await store.seed_async({"my-agent": {"model": "m", "description": "updated"}})
        deployed_v2 = await store.get_deployed("my-agent", "system")
        assert deployed_v2 is not None
        assert deployed_v2.spec.description == "updated", (
            f"Reseed did not overwrite; got {deployed_v2.spec.description!r}.  "
            "Operators who update YAML would see no effect."
        )
        # A new version was created.
        assert deployed_v2.version_number > v1_number

    @pytest.mark.asyncio
    async def test_reseed_with_same_spec_is_idempotent(self, store: FileAgentStore):
        """Re-seeding with an unchanged spec → no new version
        created.  The contract's "only creates a new version if
        the seeded spec differs" prevents version churn on every
        restart.
        """
        await store.seed_async({"idempotent-agent": {"model": "m", "description": "same"}})
        v1 = await store.get_deployed("idempotent-agent", "system")
        assert v1 is not None

        # Same spec on second call.
        await store.seed_async({"idempotent-agent": {"model": "m", "description": "same"}})
        v2 = await store.get_deployed("idempotent-agent", "system")
        assert v2 is not None
        assert v2.version_number == v1.version_number, (
            f"Re-seeding with unchanged spec created version "
            f"{v2.version_number} vs original {v1.version_number}.  "
            "seed_async is not idempotent on equivalent specs."
        )
        assert v2.version_id == v1.version_id

    @pytest.mark.asyncio
    async def test_seed_respects_bypass_approval_even_when_gate_on(self, store: FileAgentStore):
        """Even with the governance approval gate active, YAML
        seeding bypasses it — bootstrapping must never deadlock
        waiting for admin approval.  (Docstring: "Seeded specs
        *always* bypass the approval gate".)
        """
        from agentic_primitives_gateway.config import settings

        original = settings.governance.require_admin_approval_for_deploy
        settings.governance.require_admin_approval_for_deploy = True
        try:
            await store.seed_async({"gated-agent": {"model": "m", "description": "seeded"}})
            deployed = await store.get_deployed("gated-agent", "system")
            assert deployed is not None, (
                "YAML seed produced no deployed version under the approval "
                "gate — bootstrapping would deadlock waiting for admin action."
            )
            # Status is deployed (not draft).
            assert deployed.status.value == "deployed"
        finally:
            settings.governance.require_admin_approval_for_deploy = original
