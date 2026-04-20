"""Versioned team store — File + Redis implementations.

Same structure as :mod:`versioned_agent_store`.  Fork-time ref rewriting
consults the agent store (teams reference agents by name in ``workers``,
``planner``, and ``synthesizer``), so each concrete impl is handed an agent
store reference at construction time for that lookup.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_OID, uuid5

from agentic_primitives_gateway.agents.versioned_agent_store import (
    VersionedAgentStore,
    _migrate_legacy_mapping,
)
from agentic_primitives_gateway.agents.versioned_base import (
    VersionedSpecStore,
    _StoreState,
)
from agentic_primitives_gateway.models.agents import (
    ForkRef,
    Identity,
)
from agentic_primitives_gateway.models.teams import (
    LineageNodeTeam,
    TeamLineage,
    TeamSpec,
    TeamVersion,
)

logger = logging.getLogger(__name__)

_TEAM_MIGRATION_NS = uuid5(NAMESPACE_OID, "agentic-primitives-gateway/teams")


class VersionedTeamStore(VersionedSpecStore[TeamSpec, TeamVersion]):
    """Common team-store logic."""

    _spec_cls = TeamSpec
    _version_cls = TeamVersion
    _entity_label = "team"
    _version_name_field = "team_name"

    def __init__(self) -> None:
        self._agent_store: VersionedAgentStore | None = None

    def bind_agent_store(self, agent_store: VersionedAgentStore) -> None:
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


class FileVersionedTeamStore(VersionedTeamStore):
    def __init__(self, path: str = "teams.json") -> None:
        super().__init__()
        self._path = Path(path)
        self._lock = threading.Lock()
        self._state = self._load_from_disk()

    def _load_from_disk(self) -> _StoreState:
        if not self._path.exists():
            return _StoreState()
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            logger.exception("Failed to parse %s; starting empty", self._path)
            return _StoreState()
        if isinstance(data, dict) and "versions" in data:
            return _StoreState.from_json(data)
        return _StoreState()

    async def _load_state(self) -> _StoreState:
        return self._state

    async def _save_state(self, state: _StoreState) -> None:
        with self._lock:
            self._state = state
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(state.to_json(), indent=2, default=str))
            tmp.replace(self._path)

    async def migrate_from_legacy(self) -> int:
        if not self._path.exists():
            return 0
        try:
            raw = json.loads(self._path.read_text())
        except Exception:
            return 0
        if not isinstance(raw, dict) or "versions" in raw:
            return 0
        migrated = _migrate_legacy_mapping(
            raw,
            state=self._state,
            version_cls_name=self._entity_label,
            migration_ns=_TEAM_MIGRATION_NS,
            version_name_field=self._version_name_field,
        )
        await self._save_state(self._state)
        return migrated


class RedisVersionedTeamStore(VersionedTeamStore):
    _NAMESPACE_PREFIX = "gateway:teams"
    _LEGACY_HASH = "gateway:teams"

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        import redis.asyncio as aioredis

        super().__init__()
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._redis_url = redis_url
        logger.info(
            "%s initialized (url=%s)",
            type(self).__name__,
            redis_url.split("@")[-1],
        )

    async def _load_state(self) -> _StoreState:
        pipe = self._redis.pipeline()
        pipe.hgetall(f"{self._NAMESPACE_PREFIX}:versions")
        pipe.hgetall(f"{self._NAMESPACE_PREFIX}:identities")
        pipe.lrange(f"{self._NAMESPACE_PREFIX}:proposals", 0, -1)
        versions_raw, identities_raw, proposals = await pipe.execute()
        state = _StoreState()
        state.versions = {k: json.loads(v) for k, v in versions_raw.items()}
        state.identities = {k: json.loads(v) for k, v in identities_raw.items()}
        state.proposals = list(proposals)
        return state

    async def _save_state(self, state: _StoreState) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(f"{self._NAMESPACE_PREFIX}:versions")
        if state.versions:
            pipe.hset(
                f"{self._NAMESPACE_PREFIX}:versions",
                mapping={k: json.dumps(v, default=str) for k, v in state.versions.items()},
            )
        pipe.delete(f"{self._NAMESPACE_PREFIX}:identities")
        if state.identities:
            pipe.hset(
                f"{self._NAMESPACE_PREFIX}:identities",
                mapping={k: json.dumps(v, default=str) for k, v in state.identities.items()},
            )
        pipe.delete(f"{self._NAMESPACE_PREFIX}:proposals")
        if state.proposals:
            pipe.rpush(f"{self._NAMESPACE_PREFIX}:proposals", *state.proposals)
        await pipe.execute()

    async def migrate_from_legacy(self) -> int:
        legacy_raw: dict[str, str] = await self._redis.hgetall(self._LEGACY_HASH)  # type: ignore[misc]
        if not legacy_raw:
            return 0
        state = await self._load_state()
        legacy_mapping = {k: json.loads(v) for k, v in legacy_raw.items()}
        migrated = _migrate_legacy_mapping(
            legacy_mapping,
            state=state,
            version_cls_name=self._entity_label,
            migration_ns=_TEAM_MIGRATION_NS,
            version_name_field=self._version_name_field,
        )
        if migrated:
            await self._save_state(state)
            await self._redis.delete(self._LEGACY_HASH)
        return migrated
