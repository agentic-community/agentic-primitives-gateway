from __future__ import annotations

import builtins
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from agentic_primitives_gateway.auth.access import check_access
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.models.agents import AgentSpec

logger = logging.getLogger(__name__)


class AgentStore(ABC):
    """Abstract base for agent spec persistence."""

    @abstractmethod
    async def get(self, name: str) -> AgentSpec | None: ...

    @abstractmethod
    async def list(self) -> list[AgentSpec]: ...

    async def list_for_user(self, principal: AuthenticatedPrincipal) -> builtins.list[AgentSpec]:
        """List agents accessible to the given principal.

        Default implementation loads all agents and filters by ownership/groups.
        Backends may override for more efficient filtering.
        """
        all_specs = await self.list()
        return [s for s in all_specs if check_access(principal, s.owner_id, s.shared_with)]

    @abstractmethod
    async def create(self, spec: AgentSpec) -> AgentSpec: ...

    @abstractmethod
    async def update(self, name: str, updates: dict[str, Any]) -> AgentSpec: ...

    @abstractmethod
    async def delete(self, name: str) -> bool: ...

    def create_background_run_manager(self, **kwargs: Any) -> Any:
        """Create a BackgroundRunManager with this store's event persistence.

        Override in backends that support cross-replica event stores (e.g. Redis).
        Returns None if no special manager is needed (in-memory default is used).
        """
        return None

    def create_session_registry(self) -> Any:
        """Create a SessionRegistry for this backend.

        Override in backends that support cross-replica session tracking.
        Returns None if no registry is needed (in-memory default is used).
        """
        return None

    def create_checkpoint_store(self) -> Any:
        """Create a CheckpointStore for durable run execution.

        Override in backends that support cross-replica state persistence.
        Returns None if checkpointing is not supported (runs are ephemeral).
        """
        return None


class FileAgentStore(AgentStore):
    """JSON-file-backed agent store.

    Loads from disk on init, writes on every mutation.
    Supports seeding from config without overwriting existing agents.
    """

    def __init__(self, path: str = "agents.json") -> None:
        self._path = Path(path)
        self._agents: dict[str, AgentSpec] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                for name, spec_dict in data.items():
                    self._agents[name] = AgentSpec(**spec_dict)
                logger.info("Loaded %d agents from %s", len(self._agents), self._path)
            except Exception:
                logger.exception("Failed to load agents from %s", self._path)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {name: spec.model_dump() for name, spec in self._agents.items()}
        self._path.write_text(json.dumps(data, indent=2, default=str))

    def seed(self, specs: dict[str, dict[str, Any]]) -> None:
        """Seed agents from YAML config. Overwrites existing agents from config.

        Config-seeded agents default to ``shared_with: ["*"]`` (accessible
        to all authenticated users) unless the config explicitly sets it.
        """
        count = 0
        for name, spec_dict in specs.items():
            spec_dict.setdefault("shared_with", ["*"])
            spec_dict.setdefault("checkpointing_enabled", True)
            new_spec = AgentSpec(name=name, **spec_dict)
            existing = self._agents.get(name)
            if existing is None or existing != new_spec:
                self._agents[name] = new_spec
                count += 1
        if count:
            self._save()
            logger.info("Seeded/updated %d agents from config", count)

    async def get(self, name: str) -> AgentSpec | None:
        return self._agents.get(name)

    async def list(self) -> list[AgentSpec]:
        return list(self._agents.values())

    async def create(self, spec: AgentSpec) -> AgentSpec:
        self._agents[spec.name] = spec
        self._save()
        return spec

    async def update(self, name: str, updates: dict[str, Any]) -> AgentSpec:
        existing = self._agents.get(name)
        if existing is None:
            raise KeyError(f"Agent not found: {name}")
        updated_data = existing.model_dump()
        updated_data.update(updates)
        updated_spec = AgentSpec(**updated_data)
        self._agents[name] = updated_spec
        self._save()
        return updated_spec

    async def delete(self, name: str) -> bool:
        if name in self._agents:
            del self._agents[name]
            self._save()
            return True
        return False
