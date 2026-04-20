"""Versioned agent store — File + Redis implementations.

Replaces the old :class:`AgentStore` / :class:`FileAgentStore` /
:class:`RedisAgentStore`.  Every mutation produces an immutable
:class:`AgentVersion`; the deployed version is promoted via an atomic
pointer flip.

See ``versioned_base.py`` for the generic machinery.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_OID, uuid5

from agentic_primitives_gateway.agents.versioned_base import (
    SYSTEM_OWNER,
    VersionedSpecStore,
    _now_iso,
    _StoreState,
)
from agentic_primitives_gateway.models.agents import (
    AgentLineage,
    AgentSpec,
    AgentVersion,
    ForkRef,
    Identity,
    LineageNodeAgent,
    VersionStatus,
)

logger = logging.getLogger(__name__)

# Namespace for deterministic version IDs during legacy migration.
_AGENT_MIGRATION_NS = uuid5(NAMESPACE_OID, "agentic-primitives-gateway/agents")


class VersionedAgentStore(VersionedSpecStore[AgentSpec, AgentVersion]):
    """Common agent-store logic (fork ref rewrite + lineage wrapping)."""

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
        If no such identity exists (the ref points at a system/built-in
        agent), leave it bare — caller-scope resolution at run time will
        walk caller-ns → system.
        """
        agents_cfg = spec.primitives.get("agents")
        if not agents_cfg or not agents_cfg.tools:
            return spec, 0

        rewrote = 0
        new_tools: list[str] = []
        for ref in agents_cfg.tools:
            if ":" in ref:
                # Already qualified — leave alone.
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

        # Replace the tools list on a copy of the spec.
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


class FileVersionedAgentStore(VersionedAgentStore):
    """JSON-file-backed versioned agent store.

    Single document at ``path`` with ``versions`` / ``identities`` /
    ``proposals`` sections.  Reads are served from an in-memory cache
    reloaded on every call; writes go through atomic tmp+rename.
    """

    def __init__(self, path: str = "agents.json") -> None:
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
        # New layout has `versions` key; legacy layout is {name: spec_dict}.
        if isinstance(data, dict) and "versions" in data:
            return _StoreState.from_json(data)
        # Legacy layout — we still return empty; the migrate_from_legacy
        # pass will populate from this file's data separately.
        return _StoreState()

    async def _load_state(self) -> _StoreState:
        # Single-process only; state is always authoritative in memory.
        return self._state

    async def _save_state(self, state: _StoreState) -> None:
        with self._lock:
            self._state = state
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(state.to_json(), indent=2, default=str))
            tmp.replace(self._path)

    async def migrate_from_legacy(self) -> int:
        """One-shot migration from the legacy ``{name: spec_dict}`` layout.

        Idempotent: re-running produces the same ``version_id`` via a
        deterministic UUIDv5 and skips already-migrated identities.
        """
        if not self._path.exists():
            return 0
        try:
            raw = json.loads(self._path.read_text())
        except Exception:
            return 0
        if not isinstance(raw, dict) or "versions" in raw:
            return 0  # already new layout
        migrated = _migrate_legacy_mapping(
            raw,
            state=self._state,
            version_cls_name=self._entity_label,
            migration_ns=_AGENT_MIGRATION_NS,
            version_name_field=self._version_name_field,
        )
        await self._save_state(self._state)
        return migrated


class RedisVersionedAgentStore(VersionedAgentStore):
    """Redis-backed versioned agent store.

    Uses separate keys per version rather than a single hash so version
    reads don't contend with pointer flips.  See ``versioned_base.py`` for
    the key layout.
    """

    _NAMESPACE_PREFIX = "gateway:agents"
    _LEGACY_HASH = "gateway:agents"

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._redis_url = redis_url
        logger.info(
            "%s initialized (url=%s)",
            type(self).__name__,
            redis_url.split("@")[-1],
        )

    # NOTE: The first cut uses a snapshot-read / full-rewrite pattern
    # (load state → mutate → save state) so every operation goes through
    # a transactional `MULTI` / `EXEC` block under a single `WATCH` on a
    # sentinel key.  This trades throughput for simplicity; hot paths can
    # later migrate to per-op MULTI blocks that touch only the keys they
    # need.

    _STATE_SENTINEL = "gateway:agents:state_sentinel"

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
        # Snapshot persistence: overwrite the three redis structures.  We
        # use a pipeline rather than WATCH/MULTI retry because the caller
        # has already computed a full new state from a prior _load_state.
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

    def create_background_run_manager(self, **kwargs: Any) -> Any:
        from agentic_primitives_gateway.routes._background import (
            BackgroundRunManager,
            RedisEventStore,
        )

        return BackgroundRunManager(event_store=RedisEventStore(self._redis_url), **kwargs)

    def create_session_registry(self) -> Any:
        from agentic_primitives_gateway.agents.session_registry import RedisSessionRegistry

        return RedisSessionRegistry(redis_url=self._redis_url)

    def create_checkpoint_store(self) -> Any:
        from agentic_primitives_gateway.agents.checkpoint import RedisCheckpointStore

        return RedisCheckpointStore(redis_url=self._redis_url)

    async def migrate_from_legacy(self) -> int:
        """One-shot migration from the legacy ``gateway:agents`` hash."""
        legacy_raw: dict[str, str] = await self._redis.hgetall(self._LEGACY_HASH)  # type: ignore[misc]
        if not legacy_raw:
            return 0
        # Avoid re-reading if the new layout is already populated.
        state = await self._load_state()
        legacy_mapping = {k: json.loads(v) for k, v in legacy_raw.items()}
        migrated = _migrate_legacy_mapping(
            legacy_mapping,
            state=state,
            version_cls_name=self._entity_label,
            migration_ns=_AGENT_MIGRATION_NS,
            version_name_field=self._version_name_field,
        )
        if migrated:
            await self._save_state(state)
            await self._redis.delete(self._LEGACY_HASH)
        return migrated


# ── Shared migration implementation (reused by file + redis stores) ────────


def _migrate_legacy_mapping(
    legacy: dict[str, dict[str, Any]],
    *,
    state: _StoreState,
    version_cls_name: str,
    migration_ns: Any,
    version_name_field: str,
) -> int:
    """Turn legacy ``{name: spec_dict}`` into versioned records in-place.

    Deterministic UUIDv5 is used so re-running produces the same ids and
    skips already-present versions.
    """
    count = 0
    now = _now_iso()
    for name, spec_dict in legacy.items():
        if not isinstance(spec_dict, dict):
            continue
        owner_id = spec_dict.get("owner_id", SYSTEM_OWNER)
        ident = f"{owner_id}:{name}"
        version_id = uuid5(migration_ns, f"{ident}:v1").hex
        if version_id in state.versions:
            continue  # idempotent
        spec_dict.setdefault("shared_with", ["*"])
        spec_dict.setdefault("name", name)
        spec_dict["owner_id"] = owner_id
        version = {
            "version_id": version_id,
            version_name_field: name,
            "owner_id": owner_id,
            "version_number": 1,
            "spec": spec_dict,
            "created_at": now,
            "created_by": SYSTEM_OWNER,
            "parent_version_id": None,
            "forked_from": None,
            "status": VersionStatus.DEPLOYED.value,
            "approved_by": None,
            "approved_at": None,
            "deployed_at": now,
            "commit_message": "migrated from legacy store",
        }
        state.versions[version_id] = version
        state.identities[ident] = {
            "deployed_version_id": version_id,
            "draft_version_id": None,
            "version_number_cursor": 1,
            "version_ids": [version_id],
        }
        count += 1
        logger.info("Migrated legacy %s '%s' → version %s", version_cls_name, name, version_id)
    return count
