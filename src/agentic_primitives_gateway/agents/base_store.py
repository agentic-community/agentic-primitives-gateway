"""Generic versioned spec store.

Shared machinery for :class:`AgentStore` and :class:`TeamStore`.
Each identity is the pair ``(owner_id, name)``.  Versions are immutable records;
the store atomically flips a ``deployed`` pointer to the active version.

Subclasses supply:

* ``_spec_cls`` — the Pydantic model embedded in each version
  (:class:`AgentSpec` or :class:`TeamSpec`)
* ``_version_cls`` — the version wrapper model
  (:class:`AgentVersion` or :class:`TeamVersion`)
* ``_entity_label`` — "agent" / "team" (used in logs, error messages)
* ``_version_name_field`` — name of the field on the version that stores the
  spec's name (``agent_name`` or ``team_name``)
* ``_rewrite_sub_refs`` — spec-type-specific fork-time reference rewriting
  (no-op for teams that only reference agent names; a custom walk for agent
  specs that reference other agents via ``primitives.agents.tools``).
"""

from __future__ import annotations

import builtins
import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar, cast
from uuid import uuid4

from pydantic import BaseModel

from agentic_primitives_gateway.auth.access import check_access
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.models.agents import (
    ForkRef,
    VersionStatus,
)

logger = logging.getLogger(__name__)


SpecT = TypeVar("SpecT", bound=BaseModel)
VersionT = TypeVar("VersionT", bound=BaseModel)


SYSTEM_OWNER = "system"


class _StoreState:
    """In-memory, authoritative representation of the versioned store.

    Used verbatim by the file-backed store (serialized to JSON on write) and
    as a caching layer by the Redis-backed store where Redis is the source of
    truth.  Methods on this class are pure and side-effect free apart from
    mutating ``self``.
    """

    def __init__(self) -> None:
        # version_id -> raw version dict
        self.versions: dict[str, dict[str, Any]] = {}
        # identity qualified key "{owner}:{name}" -> identity metadata
        self.identities: dict[str, dict[str, Any]] = {}
        # proposal qualified key list
        self.proposals: list[str] = []

    def to_json(self) -> dict[str, Any]:
        return {
            "versions": self.versions,
            "identities": self.identities,
            "proposals": self.proposals,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> _StoreState:
        s = cls()
        s.versions = data.get("versions", {}) or {}
        s.identities = data.get("identities", {}) or {}
        s.proposals = data.get("proposals", []) or []
        return s


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _identity_key(owner_id: str, name: str) -> str:
    return f"{owner_id}:{name}"


class SpecStore(ABC, Generic[SpecT, VersionT]):
    """Abstract versioned store for agents or teams.

    Implementations persist the same logical shape — a set of immutable
    ``VersionT`` records keyed by ``version_id``, a mapping from
    ``(owner_id, name)`` identities to ``deployed_version_id`` and
    ``draft_version_id`` pointers, and a list of pending proposals.

    All methods are async for uniform use across file and redis backends.
    """

    _spec_cls: type[SpecT]
    _version_cls: type[VersionT]
    _entity_label: str
    _version_name_field: str  # "agent_name" or "team_name"

    # ── Low-level state I/O (concrete backends implement these) ────────────

    @abstractmethod
    async def _load_state(self) -> _StoreState:
        """Return a snapshot of the store state."""

    @abstractmethod
    async def _save_state(self, state: _StoreState) -> None:
        """Persist a full snapshot of the store state.

        Implementations may optimize to only write deltas; the default
        contract is that ``state`` fully describes the store after this
        call returns.
        """

    # ── Fork-time reference rewriting (per-spec logic) ─────────────────────

    def _rewrite_sub_refs(
        self,
        spec: SpecT,
        source_owner_id: str,
        agent_identities: set[str],
    ) -> tuple[SpecT, int]:
        """Rewrite bare sub-references to qualified ``{source_owner}:{name}``
        form, if a matching identity exists in the source namespace.

        Default implementation returns ``spec`` unchanged (used by team-worker
        refs; the agent store overrides this to walk ``primitives.agents.tools``).

        ``agent_identities`` is a pre-computed set of qualified identity keys
        for the agent store, so both stores can reuse it.  Returns
        ``(new_spec, rewrite_count)``.
        """
        return spec, 0

    # ── Derived spec accessors ─────────────────────────────────────────────

    def _version_spec(self, version: dict[str, Any]) -> SpecT:
        return self._spec_cls(**version["spec"])

    def _current_spec_owner_id(self, spec: SpecT) -> str:
        return getattr(spec, "owner_id", SYSTEM_OWNER)

    # ── Core version CRUD ──────────────────────────────────────────────────

    async def create_version(
        self,
        *,
        name: str,
        owner_id: str,
        spec: SpecT,
        created_by: str,
        parent_version_id: str | None = None,
        forked_from: ForkRef | None = None,
        commit_message: str | None = None,
        auto_deploy: bool = True,
        bypass_approval: bool = False,
    ) -> VersionT:
        """Create a new immutable version.

        Deploy semantics:

        * ``auto_deploy=True`` + approval gate OFF → new version deployed immediately.
        * ``auto_deploy=True`` + approval gate ON + ``bypass_approval=True`` →
          new version deployed immediately (used for seed + migration paths).
        * ``auto_deploy=True`` + approval gate ON → new version saved as ``draft``;
          caller must propose/approve/deploy through the explicit flow.
        * ``auto_deploy=False`` → always saved as ``draft``.
        """
        from agentic_primitives_gateway.config import settings

        state = await self._load_state()
        ident = _identity_key(owner_id, name)
        identity_meta = state.identities.setdefault(
            ident,
            {
                "deployed_version_id": None,
                "draft_version_id": None,
                "version_number_cursor": 0,
                "version_ids": [],
            },
        )

        identity_meta["version_number_cursor"] = int(identity_meta["version_number_cursor"]) + 1
        version_number = identity_meta["version_number_cursor"]
        version_id = uuid4().hex

        approval_required = settings.governance.require_admin_approval_for_deploy and not bypass_approval
        now = _now_iso()
        will_deploy = auto_deploy and not approval_required

        status = VersionStatus.DEPLOYED if will_deploy else VersionStatus.DRAFT

        # Ensure owner_id on the embedded spec matches the identity.
        spec_data = spec.model_dump()
        spec_data["owner_id"] = owner_id
        spec_data["name"] = name
        normalized_spec = self._spec_cls(**spec_data)

        version: dict[str, Any] = {
            "version_id": version_id,
            self._version_name_field: name,
            "owner_id": owner_id,
            "version_number": version_number,
            "spec": normalized_spec.model_dump(mode="json"),
            "created_at": now,
            "created_by": created_by,
            "parent_version_id": parent_version_id,
            "forked_from": forked_from.model_dump() if forked_from is not None else None,
            "status": status.value,
            "approved_by": None,
            "approved_at": None,
            "deployed_at": now if will_deploy else None,
            "commit_message": commit_message,
        }

        state.versions[version_id] = version
        identity_meta["version_ids"].append(version_id)

        if will_deploy:
            previous_id = identity_meta.get("deployed_version_id")
            if previous_id and previous_id in state.versions:
                state.versions[previous_id]["status"] = VersionStatus.ARCHIVED.value
            identity_meta["deployed_version_id"] = version_id
            # Clear any outstanding draft for this identity; a successful
            # deploy supersedes the in-progress edit.
            identity_meta["draft_version_id"] = None
        else:
            identity_meta["draft_version_id"] = version_id

        self._apply_retention(state, ident, owner_id, name)
        await self._save_state(state)
        return self._version_cls(**version)

    def _apply_retention(
        self,
        state: _StoreState,
        ident: str,
        owner_id: str,
        name: str,
    ) -> None:
        """Archive the oldest non-deployed, non-draft versions once the
        identity's history exceeds ``max_versions_per_identity``.
        """
        from agentic_primitives_gateway.config import settings

        cap = self._retention_cap(settings)
        identity_meta = state.identities[ident]
        version_ids: list[str] = identity_meta["version_ids"]
        protected = {
            identity_meta.get("deployed_version_id"),
            identity_meta.get("draft_version_id"),
        }
        # Iterate in insertion (chronological) order, archive oldest first
        # until we fit under the cap.
        excess = max(0, len(version_ids) - cap)
        archived = 0
        for vid in version_ids:
            if archived >= excess:
                break
            if vid in protected:
                continue
            v = state.versions.get(vid)
            if v is None:
                continue
            if v["status"] in (VersionStatus.DEPLOYED.value, VersionStatus.DRAFT.value):
                continue
            if v["status"] == VersionStatus.ARCHIVED.value:
                continue
            v["status"] = VersionStatus.ARCHIVED.value
            archived += 1

    def _retention_cap(self, settings: Any) -> int:
        # Overridden by agent / team subclasses to pull from the correct config.
        return int(getattr(settings.agents, "max_versions_per_identity", 50))

    async def get_version(self, name: str, owner_id: str, version_id: str) -> VersionT | None:
        state = await self._load_state()
        v = state.versions.get(version_id)
        if v is None:
            return None
        if v.get("owner_id") != owner_id or v.get(self._version_name_field) != name:
            return None
        return self._version_cls(**v)

    async def list_versions(self, name: str, owner_id: str) -> list[VersionT]:
        state = await self._load_state()
        ident = _identity_key(owner_id, name)
        identity_meta = state.identities.get(ident)
        if identity_meta is None:
            return []
        out: list[VersionT] = []
        for vid in identity_meta["version_ids"]:
            v = state.versions.get(vid)
            if v is not None:
                out.append(self._version_cls(**v))
        return out

    async def get_deployed(self, name: str, owner_id: str) -> VersionT | None:
        state = await self._load_state()
        ident = _identity_key(owner_id, name)
        identity_meta = state.identities.get(ident)
        if identity_meta is None:
            return None
        deployed_id = identity_meta.get("deployed_version_id")
        if not deployed_id:
            return None
        v = state.versions.get(deployed_id)
        if v is None:
            return None
        return self._version_cls(**v)

    async def get_draft(self, name: str, owner_id: str) -> VersionT | None:
        state = await self._load_state()
        ident = _identity_key(owner_id, name)
        identity_meta = state.identities.get(ident)
        if identity_meta is None:
            return None
        draft_id = identity_meta.get("draft_version_id")
        if not draft_id:
            return None
        v = state.versions.get(draft_id)
        if v is None:
            return None
        return self._version_cls(**v)

    async def deploy_version(self, name: str, owner_id: str, version_id: str, *, deployed_by: str) -> VersionT:
        """Atomically flip the deployed pointer to ``version_id``.

        Under approval mode, the target must be in ``approved``/``proposed``
        → caller should have gone through ``approve_version`` first.  We
        additionally accept ``draft`` here so that when approval is OFF the
        standard "create draft → deploy" flow still works.
        """
        from agentic_primitives_gateway.config import settings

        state = await self._load_state()
        ident = _identity_key(owner_id, name)
        identity_meta = state.identities.get(ident)
        if identity_meta is None:
            raise KeyError(f"{self._entity_label.capitalize()} identity not found: {ident}")
        v = state.versions.get(version_id)
        if v is None or v.get("owner_id") != owner_id or v.get(self._version_name_field) != name:
            raise KeyError(f"Version not found: {version_id}")

        status = v["status"]
        if settings.governance.require_admin_approval_for_deploy:
            # Approval gate: must be proposed+approved before deploy.
            if status not in (VersionStatus.PROPOSED.value, VersionStatus.DEPLOYED.value):
                raise ValueError(
                    f"Cannot deploy version in status '{status}' while approval gate is active",
                )
            if status == VersionStatus.PROPOSED.value and not v.get("approved_by"):
                raise ValueError("Version must be approved before deploy")

        previous_id = identity_meta.get("deployed_version_id")
        if previous_id and previous_id != version_id and previous_id in state.versions:
            state.versions[previous_id]["status"] = VersionStatus.ARCHIVED.value

        v["status"] = VersionStatus.DEPLOYED.value
        v["deployed_at"] = _now_iso()
        identity_meta["deployed_version_id"] = version_id
        if identity_meta.get("draft_version_id") == version_id:
            identity_meta["draft_version_id"] = None
        # Remove from proposals if present.
        proposal_key = f"{owner_id}:{name}:{version_id}"
        if proposal_key in state.proposals:
            state.proposals.remove(proposal_key)

        await self._save_state(state)
        return self._version_cls(**v)

    async def archive_version(self, name: str, owner_id: str, version_id: str) -> None:
        state = await self._load_state()
        ident = _identity_key(owner_id, name)
        identity_meta = state.identities.get(ident)
        if identity_meta is None:
            return
        v = state.versions.get(version_id)
        if v is None:
            return
        v["status"] = VersionStatus.ARCHIVED.value
        if identity_meta.get("deployed_version_id") == version_id:
            identity_meta["deployed_version_id"] = None
        if identity_meta.get("draft_version_id") == version_id:
            identity_meta["draft_version_id"] = None
        proposal_key = f"{owner_id}:{name}:{version_id}"
        if proposal_key in state.proposals:
            state.proposals.remove(proposal_key)
        await self._save_state(state)

    async def archive_identity(self, name: str, owner_id: str) -> int:
        """Archive all versions for an identity (used by DELETE).

        Returns the number of versions transitioned to archived.
        """
        state = await self._load_state()
        ident = _identity_key(owner_id, name)
        identity_meta = state.identities.get(ident)
        if identity_meta is None:
            return 0
        count = 0
        for vid in identity_meta.get("version_ids", []):
            v = state.versions.get(vid)
            if v is None:
                continue
            if v["status"] != VersionStatus.ARCHIVED.value:
                v["status"] = VersionStatus.ARCHIVED.value
                count += 1
        identity_meta["deployed_version_id"] = None
        identity_meta["draft_version_id"] = None
        # Drop from proposal list too.
        state.proposals = [p for p in state.proposals if not p.startswith(f"{owner_id}:{name}:")]
        await self._save_state(state)
        return count

    # ── Approval workflow ──────────────────────────────────────────────────

    async def propose_version(self, name: str, owner_id: str, version_id: str) -> VersionT:
        state = await self._load_state()
        v = state.versions.get(version_id)
        if v is None or v.get("owner_id") != owner_id or v.get(self._version_name_field) != name:
            raise KeyError(f"Version not found: {version_id}")
        if v["status"] != VersionStatus.DRAFT.value:
            raise ValueError(f"Only draft versions can be proposed (current: {v['status']})")
        v["status"] = VersionStatus.PROPOSED.value
        key = f"{owner_id}:{name}:{version_id}"
        if key not in state.proposals:
            state.proposals.append(key)
        await self._save_state(state)
        return self._version_cls(**v)

    async def approve_version(self, name: str, owner_id: str, version_id: str, *, approver_id: str) -> VersionT:
        state = await self._load_state()
        v = state.versions.get(version_id)
        if v is None or v.get("owner_id") != owner_id or v.get(self._version_name_field) != name:
            raise KeyError(f"Version not found: {version_id}")
        if v["status"] != VersionStatus.PROPOSED.value:
            raise ValueError(f"Only proposed versions can be approved (current: {v['status']})")
        v["approved_by"] = approver_id
        v["approved_at"] = _now_iso()
        # Status stays PROPOSED; caller flips to DEPLOYED via deploy_version
        # so that approval and deploy-rollout remain separable actions.
        await self._save_state(state)
        return self._version_cls(**v)

    async def reject_version(
        self,
        name: str,
        owner_id: str,
        version_id: str,
        *,
        approver_id: str,
        reason: str,
    ) -> VersionT:
        state = await self._load_state()
        v = state.versions.get(version_id)
        if v is None or v.get("owner_id") != owner_id or v.get(self._version_name_field) != name:
            raise KeyError(f"Version not found: {version_id}")
        if v["status"] != VersionStatus.PROPOSED.value:
            raise ValueError(f"Only proposed versions can be rejected (current: {v['status']})")
        v["status"] = VersionStatus.REJECTED.value
        v["approved_by"] = approver_id  # "rejected_by"; the approval-chain table is the audit log
        v["approved_at"] = _now_iso()
        key = f"{owner_id}:{name}:{version_id}"
        if key in state.proposals:
            state.proposals.remove(key)
        await self._save_state(state)
        return self._version_cls(**v)

    async def list_pending_proposals(self) -> list[VersionT]:
        state = await self._load_state()
        out: list[VersionT] = []
        for key in state.proposals:
            owner_id, name, version_id = key.split(":", 2)
            v = state.versions.get(version_id)
            if (
                v is None
                or v.get("status") != VersionStatus.PROPOSED.value
                or v.get("owner_id") != owner_id
                or v.get(self._version_name_field) != name
            ):
                continue
            out.append(self._version_cls(**v))
        return out

    # ── Fork + lineage ─────────────────────────────────────────────────────

    async def _all_agent_identities(self) -> set[str]:
        """Return qualified identity keys for the *agent* store.

        The team store overrides ``_rewrite_sub_refs`` and calls through to
        the agent store; the agent store uses its own identity set.  Default:
        empty set (teams have no fork-time rewriting of their own type).
        """
        state = await self._load_state()
        return set(state.identities.keys())

    async def fork(
        self,
        *,
        source_name: str,
        source_owner_id: str,
        target_owner_id: str,
        target_name: str | None = None,
        created_by: str,
        commit_message: str | None = None,
    ) -> VersionT:
        """Clone the deployed version of ``(source_owner, source_name)``
        into ``target_owner_id`` namespace as a fresh version 1.

        Fork auto-qualifies sub-references against the source namespace
        via :meth:`_rewrite_sub_refs` — see subclass docs.
        """
        source = await self.get_deployed(source_name, source_owner_id)
        if source is None:
            raise KeyError(f"Cannot fork {self._entity_label} {source_owner_id}:{source_name}: no deployed version")
        tgt_name = target_name or source_name
        # Collision check.
        existing = await self.get_deployed(tgt_name, target_owner_id)
        if existing is not None:
            raise KeyError(
                f"{self._entity_label.capitalize()} already exists in target namespace: {target_owner_id}:{tgt_name}"
            )

        source_spec: SpecT = source.spec  # type: ignore[attr-defined]
        agent_identities = await self._agent_identity_keys_for_fork()
        rewritten_spec, rewrote_refs = self._rewrite_sub_refs(source_spec, source_owner_id, agent_identities)

        # Re-home the spec to the target owner/name.
        spec_dict = rewritten_spec.model_dump()
        spec_dict["owner_id"] = target_owner_id
        spec_dict["name"] = tgt_name
        new_spec = self._spec_cls(**spec_dict)

        forked_from = ForkRef(
            name=source_name,
            owner_id=source_owner_id,
            version_id=source.version_id,  # type: ignore[attr-defined]
        )
        version = await self.create_version(
            name=tgt_name,
            owner_id=target_owner_id,
            spec=new_spec,
            created_by=created_by,
            parent_version_id=None,
            forked_from=forked_from,
            commit_message=commit_message,
            auto_deploy=True,
        )
        logger.info(
            "forked %s %s:%s -> %s:%s (rewrote_refs=%d)",
            self._entity_label,
            source_owner_id,
            source_name,
            target_owner_id,
            tgt_name,
            rewrote_refs,
        )
        return version

    async def _agent_identity_keys_for_fork(self) -> set[str]:
        """Hook for subclasses: return the set of *agent* identity keys used
        by fork-time ref rewriting.  The team store overrides this to poll the
        agent store.  Default: this store's own identities.
        """
        return await self._all_agent_identities()

    async def get_lineage(self, name: str, owner_id: str) -> dict[str, Any]:
        """Return the lineage DAG rooted at ``(owner_id, name)``.

        Returns a raw dict so the agent/team stores can wrap it in their own
        ``AgentLineage``/``TeamLineage`` types.
        """
        state = await self._load_state()

        visited: set[str] = set()  # qualified identity keys
        nodes: list[dict[str, Any]] = []
        deployed_map: dict[str, str] = {}

        stack: list[str] = [_identity_key(owner_id, name)]
        while stack:
            ident = stack.pop()
            if ident in visited:
                continue
            visited.add(ident)
            identity_meta = state.identities.get(ident)
            if identity_meta is None:
                continue
            if identity_meta.get("deployed_version_id"):
                deployed_map[ident] = identity_meta["deployed_version_id"]

            for vid in identity_meta.get("version_ids", []):
                v = state.versions.get(vid)
                if v is None:
                    continue
                # children = versions whose parent_version_id == this one
                # (same identity by definition of parent link).
                children_ids = [
                    other_id
                    for other_id in identity_meta["version_ids"]
                    if state.versions.get(other_id, {}).get("parent_version_id") == vid
                ]
                # forks_out = version records in any identity whose
                # forked_from.version_id == this.
                forks_out: list[ForkRef] = []
                for other_state_id, other_v in state.versions.items():
                    ff = other_v.get("forked_from")
                    if ff and ff.get("version_id") == vid:
                        forks_out.append(
                            ForkRef(
                                name=other_v[self._version_name_field],
                                owner_id=other_v["owner_id"],
                                version_id=other_state_id,
                            )
                        )
                        stack.append(_identity_key(other_v["owner_id"], other_v[self._version_name_field]))
                # Add parent-forked source to traversal
                ff = v.get("forked_from")
                if ff:
                    stack.append(_identity_key(ff["owner_id"], ff["name"]))
                nodes.append(
                    {
                        "version": v,
                        "children_ids": children_ids,
                        "forks_out": [f.model_dump() for f in forks_out],
                    }
                )

        return {
            "root_identity": {"owner_id": owner_id, "name": name},
            "nodes": nodes,
            "deployed": deployed_map,
        }

    # ── Resolution ─────────────────────────────────────────────────────────

    async def resolve_for_caller(self, name: str, principal: AuthenticatedPrincipal) -> SpecT | None:
        """Caller-context resolution: ``(principal.id, name)`` → ``("system", name)``.

        Never falls through to shared agents — qualified addressing is
        required for those.  Applies :func:`check_access` on the resolved
        spec to enforce visibility.
        """
        for owner in (principal.id, SYSTEM_OWNER):
            v = await self.get_deployed(name, owner)
            if v is None:
                continue
            spec: SpecT = v.spec  # type: ignore[attr-defined]
            spec_owner = getattr(spec, "owner_id", SYSTEM_OWNER)
            spec_shared = getattr(spec, "shared_with", [])
            if check_access(principal, spec_owner, spec_shared):
                return spec
            return None
        return None

    async def resolve_qualified(self, owner_id: str, name: str) -> SpecT | None:
        v = await self.get_deployed(name, owner_id)
        return v.spec if v is not None else None  # type: ignore[attr-defined]

    # ── Phase-1 compat: the old AgentStore surface ─────────────────────────
    #
    # Callers that pre-date caller-scoped resolution still use ``store.get``
    # / ``store.create`` / ``store.update`` / ``store.delete`` / ``store.list``.
    # These shims preserve system-namespace semantics so Phase 1 doesn't need
    # to rewrite every route.  Phase 2 replaces the call sites with
    # ``resolve_for_caller`` / ``resolve_qualified`` / ``create_version`` etc.

    async def get(self, name: str) -> SpecT | None:
        """Phase-1 compat lookup.

        Scans every namespace for an identity with this name and returns
        the first one with a deployed version.  Matches the old
        single-hash semantics where ``store.get("researcher")`` was
        unambiguous.  Phase 2 replaces this with caller-scoped resolution.
        """
        state = await self._load_state()
        for ident, meta in state.identities.items():
            ident_owner, ident_name = ident.split(":", 1)
            if ident_name != name:
                continue
            deployed_id = meta.get("deployed_version_id")
            if deployed_id and deployed_id in state.versions:
                return self._version_spec(state.versions[deployed_id])
            _ = ident_owner  # silence mypy
        return None

    async def list(self) -> builtins.list[SpecT]:
        return await self.list_all_deployed()

    async def list_for_user(self, principal: AuthenticatedPrincipal) -> builtins.list[SpecT]:
        mine, system, shared = await self.list_buckets(principal)
        return [*mine, *system, *shared]

    async def create(self, spec: SpecT) -> SpecT:
        # Phase-1 compat: routes still pass ``spec.owner_id`` through, which
        # may be either a user id (from routes/agents.py POST) or "system"
        # (from seeding).
        owner = getattr(spec, "owner_id", SYSTEM_OWNER) or SYSTEM_OWNER
        name = getattr(spec, "name", None)
        if name is None:
            raise ValueError("spec has no name")

        # Atomically claim the identity slot.  The Redis variant
        # overrides ``_try_claim_identity`` with ``HSETNX`` so two
        # replicas racing on the same name get exactly one winner — the
        # loser sees ``False`` and raises without mutating any state.
        # The file store (single process) is a no-op that always
        # returns True; the subsequent ``get_deployed`` check covers
        # same-process duplicates.
        ident = _identity_key(owner, name)
        claimed = await self._try_claim_identity(ident)
        if not claimed:
            raise KeyError(f"{self._entity_label.capitalize()} already exists: {owner}:{name}")

        try:
            existing = await self.get_deployed(name, owner)
            if existing is not None:
                raise KeyError(f"{self._entity_label.capitalize()} already exists: {owner}:{name}")
            version = await self.create_version(
                name=name,
                owner_id=owner,
                spec=spec,
                created_by=owner,
                parent_version_id=None,
                commit_message="phase-1 compat: create via old AgentStore.create()",
                auto_deploy=True,
                bypass_approval=True,
            )
        except Exception:
            # Release the claim so a retry or a different caller can
            # take the slot.  ``_release_identity_claim`` is a no-op
            # for the file store.
            await self._release_identity_claim(ident)
            raise
        return cast("SpecT", version.spec)  # type: ignore[attr-defined]

    async def _try_claim_identity(self, ident: str) -> bool:
        """Atomically reserve an identity slot.

        Returns ``True`` if the caller won the race, ``False`` if
        another caller already holds it.  Default implementation is a
        no-op (``True``) since the file store runs in a single process
        — the subsequent ``get_deployed`` check catches duplicates
        cheaply.  The Redis variant overrides with ``HSETNX`` so two
        replicas get exactly one winner.
        """
        return True

    async def _release_identity_claim(self, ident: str) -> None:
        """Undo a claim from ``_try_claim_identity``.

        Called only if ``create`` fails after claiming.  Default
        implementation is a no-op; the Redis variant deletes the
        sentinel so a retry can succeed.
        """
        return None

    async def _find_identity_by_name(self, name: str) -> tuple[str, str] | None:
        """Locate an identity by bare name, returning ``(owner_id, name)``.

        Used by the Phase-1 compat shim so ``store.update``/``store.delete``
        still work when the caller only knows the bare name.  Returns the
        first identity found — Phase 2 callers must use qualified addressing.
        """
        state = await self._load_state()
        for ident, meta in state.identities.items():
            ident_owner, ident_name = ident.split(":", 1)
            if ident_name != name:
                continue
            if meta.get("deployed_version_id"):
                return ident_owner, ident_name
        return None

    async def update(self, name: str, updates: dict[str, Any]) -> SpecT:
        # Phase-1 compat: in-place update via a new version.  Finds the
        # identity by bare name across all namespaces.
        found = await self._find_identity_by_name(name)
        if found is None:
            raise KeyError(f"{self._entity_label.capitalize()} not found: {name}")
        owner_id, _ = found
        current = await self.get_deployed(name, owner_id)
        if current is None:
            raise KeyError(f"{self._entity_label.capitalize()} not found: {name}")
        merged = current.spec.model_dump() | updates  # type: ignore[attr-defined]
        merged["owner_id"] = owner_id
        merged.setdefault("name", name)
        new_spec = self._spec_cls(**merged)
        version = await self.create_version(
            name=name,
            owner_id=owner_id,
            spec=new_spec,
            created_by=owner_id,
            parent_version_id=current.version_id,  # type: ignore[attr-defined]
            commit_message="phase-1 compat: update via old AgentStore.update()",
            auto_deploy=True,
            bypass_approval=True,
        )
        return cast("SpecT", version.spec)  # type: ignore[attr-defined]

    async def delete(self, name: str) -> bool:
        found = await self._find_identity_by_name(name)
        if found is None:
            return False
        owner_id, _ = found
        archived = await self.archive_identity(name, owner_id)
        if archived > 0:
            # Release the atomic-create claim so the name can be
            # re-created later.  No-op for the file store.
            await self._release_identity_claim(_identity_key(owner_id, name))
        return archived > 0

    # ── Bucketed listing ───────────────────────────────────────────────────

    async def list_buckets(
        self, principal: AuthenticatedPrincipal
    ) -> tuple[builtins.list[SpecT], builtins.list[SpecT], builtins.list[SpecT]]:
        """Return ``(mine, system, shared_with_me)`` deployed specs."""
        state = await self._load_state()
        mine: builtins.list[SpecT] = []
        system: builtins.list[SpecT] = []
        shared: builtins.list[SpecT] = []
        for ident, meta in state.identities.items():
            deployed_id = meta.get("deployed_version_id")
            if not deployed_id:
                continue
            v = state.versions.get(deployed_id)
            if v is None:
                continue
            spec = self._version_spec(v)
            owner = getattr(spec, "owner_id", SYSTEM_OWNER)
            if owner == principal.id:
                mine.append(spec)
            elif owner == SYSTEM_OWNER:
                if check_access(principal, owner, getattr(spec, "shared_with", [])):
                    system.append(spec)
            else:
                if check_access(principal, owner, getattr(spec, "shared_with", [])):
                    shared.append(spec)
            _ = ident  # keep ident unused-but-explicit
        return mine, system, shared

    async def list_all_deployed(self) -> builtins.list[SpecT]:
        """Return all deployed specs across all namespaces.  Admin use only."""
        state = await self._load_state()
        out: builtins.list[SpecT] = []
        for meta in state.identities.values():
            deployed_id = meta.get("deployed_version_id")
            if deployed_id and deployed_id in state.versions:
                out.append(self._version_spec(state.versions[deployed_id]))
        return out

    # ── Seeding ────────────────────────────────────────────────────────────

    async def seed_async(self, specs: dict[str, dict[str, Any]]) -> None:
        """Seed specs from YAML config into the ``system`` namespace.

        Seeded specs *always* bypass the approval gate — bootstrapping must
        never deadlock on admin approval.  Only creates a new version if the
        seeded spec differs from the currently deployed one.
        """
        count = 0
        for name, spec_dict in specs.items():
            spec_dict.setdefault("shared_with", ["*"])
            spec_dict.setdefault("checkpointing_enabled", True)
            spec_dict["owner_id"] = SYSTEM_OWNER
            spec_dict.setdefault("name", name)
            new_spec = self._spec_cls(**spec_dict)
            existing = await self.get_deployed(name, SYSTEM_OWNER)
            if existing is not None:
                existing_spec: SpecT = existing.spec  # type: ignore[attr-defined]
                if existing_spec == new_spec:
                    continue
                parent_id = existing.version_id  # type: ignore[attr-defined]
            else:
                parent_id = None
            await self.create_version(
                name=name,
                owner_id=SYSTEM_OWNER,
                spec=new_spec,
                created_by=SYSTEM_OWNER,
                parent_version_id=parent_id,
                commit_message="seeded from config",
                auto_deploy=True,
                bypass_approval=True,
            )
            count += 1
        if count:
            logger.info("Seeded/updated %d %s(s) from config", count, self._entity_label)

    def seed(self, specs: dict[str, dict[str, Any]]) -> None:
        """Synchronous entry point used by ``main.py`` lifespan startup.

        Matches the old ``SpecStore.seed`` signature: call
        :meth:`seed_async` directly if you're already inside an event loop.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.seed_async(specs))
            return
        # Inside a running loop — schedule as a task.  This mirrors the
        # ``RedisSpecStore.seed`` behavior used at bootstrap.
        loop.create_task(self.seed_async(specs))  # noqa: RUF006

    # ── Hook methods used by main.py lifespan ──────────────────────────────

    def create_background_run_manager(self, **kwargs: Any) -> Any:
        """Default: no cross-replica event store — return None."""
        return None

    def create_session_registry(self) -> Any:
        """Default: no cross-replica session registry — return None."""
        return None

    def create_checkpoint_store(self) -> Any:
        """Default: no durable checkpoint store — return None."""
        return None
