"""Agent-spec versioning logic (persistence-agnostic).

Holds :class:`AgentStore` — the spec-specific rules (fork
auto-qualification of ``primitives.agents.tools`` sub-refs, AgentLineage
wrapping) — that every backend shares.  Concrete backend-bound classes
(``FileAgentStore``, ``RedisAgentStore``) live in their own backend
modules (``file_store.py``, ``redis_store.py``) so each backend is
self-contained.

See ``base_store.py`` for the storage shape (identity index, deployed
pointer, proposal list).
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.agents.base_store import SpecStore
from agentic_primitives_gateway.models.agents import (
    AgentLineage,
    AgentSpec,
    AgentVersion,
    ForkRef,
    Identity,
    LineageNodeAgent,
)

logger = logging.getLogger(__name__)


class AgentStore(SpecStore[AgentSpec, AgentVersion]):
    """Agent-spec versioning logic (persistence supplied by a mixin)."""

    _spec_cls = AgentSpec
    _version_cls = AgentVersion
    _entity_label = "agent"
    _version_name_field = "agent_name"

    def _retention_cap(self, settings: Any) -> int:
        return int(getattr(settings.agents, "max_versions_per_identity", 50))

    def _rewrite_sub_refs(
        self,
        spec: AgentSpec,
        source_owner_id: str,
        agent_identities: set[str],
    ) -> tuple[AgentSpec, int]:
        """Qualify sub-agent refs in ``primitives["agents"].tools``.

        For each bare name in the source agent's delegation tool list, if
        ``(source_owner, name)`` exists as an identity in the agent store,
        rewrite the ref to the qualified form ``"{source_owner}:{name}"``.
        System/built-in refs stay bare — run-time caller-scope resolution
        handles those.
        """
        agents_cfg = spec.primitives.get("agents")
        if not agents_cfg or not agents_cfg.tools:
            return spec, 0

        rewrote = 0
        new_tools: list[str] = []
        for ref in agents_cfg.tools:
            if ":" in ref:
                new_tools.append(ref)
                continue
            qualified_key = f"{source_owner_id}:{ref}"
            if qualified_key in agent_identities:
                new_tools.append(qualified_key)
                rewrote += 1
            else:
                new_tools.append(ref)

        if rewrote == 0:
            return spec, 0

        spec_data = spec.model_dump()
        spec_data["primitives"]["agents"]["tools"] = new_tools
        return AgentSpec(**spec_data), rewrote

    async def get_lineage_model(self, name: str, owner_id: str) -> AgentLineage:
        raw = await self.get_lineage(name, owner_id)
        nodes: list[LineageNodeAgent] = []
        for n in raw["nodes"]:
            nodes.append(
                LineageNodeAgent(
                    version=AgentVersion(**n["version"]),
                    children_ids=n["children_ids"],
                    forks_out=[ForkRef(**f) for f in n["forks_out"]],
                )
            )
        return AgentLineage(
            root_identity=Identity(**raw["root_identity"]),
            nodes=nodes,
            deployed=raw["deployed"],
        )
