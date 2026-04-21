"""Versioned team store — File + Redis implementations.

Same pattern as :mod:`versioned_agent_store`: composes the generic
persistence mixins with team-spec logic (fork-time rewriting of
``workers`` / ``planner`` / ``synthesizer`` refs against the source
namespace, TeamLineage wrapping).

Fork-time ref rewriting consults the agent store — inject it via
:meth:`TeamStore.bind_agent_store` during lifespan.
"""

from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.agents.base_store import SpecStore
from agentic_primitives_gateway.agents.file_store import FileSpecStore
from agentic_primitives_gateway.agents.redis_store import RedisSpecStore
from agentic_primitives_gateway.agents.store import AgentStore
from agentic_primitives_gateway.models.agents import ForkRef, Identity
from agentic_primitives_gateway.models.teams import (
    LineageNodeTeam,
    TeamLineage,
    TeamSpec,
    TeamVersion,
)

logger = logging.getLogger(__name__)

_TEAM_MIGRATION_OID = "agentic-primitives-gateway/teams"


class TeamStore(SpecStore[TeamSpec, TeamVersion]):
    """Team-spec versioning logic (persistence supplied by a mixin)."""

    _spec_cls = TeamSpec
    _version_cls = TeamVersion
    _entity_label = "team"
    _version_name_field = "team_name"
    _migration_namespace_oid = _TEAM_MIGRATION_OID

    def __init__(self) -> None:
        self._agent_store: AgentStore | None = None

    def bind_agent_store(self, agent_store: AgentStore) -> None:
        """Inject the agent store used for fork-time ref rewriting."""
        self._agent_store = agent_store

    def _retention_cap(self, settings: Any) -> int:
        return int(getattr(settings.teams, "max_versions_per_identity", 50))

    def _rewrite_sub_refs(
        self,
        spec: TeamSpec,
        source_owner_id: str,
        agent_identities: set[str],
    ) -> tuple[TeamSpec, int]:
        """Qualify worker/planner/synthesizer refs against the source namespace."""
        rewrote = 0

        def _rewrite_one(ref: str) -> str:
            nonlocal rewrote
            if not ref or ":" in ref:
                return ref
            qualified = f"{source_owner_id}:{ref}"
            if qualified in agent_identities:
                rewrote += 1
                return qualified
            return ref

        new_workers = [_rewrite_one(w) for w in spec.workers]
        new_planner = _rewrite_one(spec.planner)
        new_synthesizer = _rewrite_one(spec.synthesizer)

        if rewrote == 0:
            return spec, 0

        data = spec.model_dump()
        data["workers"] = new_workers
        data["planner"] = new_planner
        data["synthesizer"] = new_synthesizer
        return TeamSpec(**data), rewrote

    async def _agent_identity_keys_for_fork(self) -> set[str]:
        if self._agent_store is None:
            return set()
        agent_state = await self._agent_store._load_state()
        return set(agent_state.identities.keys())

    async def get_lineage_model(self, name: str, owner_id: str) -> TeamLineage:
        raw = await self.get_lineage(name, owner_id)
        nodes: list[LineageNodeTeam] = []
        for n in raw["nodes"]:
            nodes.append(
                LineageNodeTeam(
                    version=TeamVersion(**n["version"]),
                    children_ids=n["children_ids"],
                    forks_out=[ForkRef(**f) for f in n["forks_out"]],
                )
            )
        return TeamLineage(
            root_identity=Identity(**raw["root_identity"]),
            nodes=nodes,
            deployed=raw["deployed"],
        )


class FileTeamStore(FileSpecStore, TeamStore):
    """File-backed versioned team store."""

    def __init__(self, path: str = "teams.json") -> None:
        TeamStore.__init__(self)
        FileSpecStore.__init__(self, path=path)


class RedisTeamStore(RedisSpecStore, TeamStore):
    """Redis-backed versioned team store."""

    _namespace_prefix = "gateway:teams"
    _legacy_hash = "gateway:teams"

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        TeamStore.__init__(self)
        RedisSpecStore.__init__(self, redis_url=redis_url)
