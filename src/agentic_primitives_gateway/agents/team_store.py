from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from agentic_primitives_gateway.models.teams import TeamSpec

logger = logging.getLogger(__name__)


class TeamStore(ABC):
    """Abstract base class for team persistence."""

    @abstractmethod
    async def get(self, name: str) -> TeamSpec | None: ...

    @abstractmethod
    async def list(self) -> list[TeamSpec]: ...

    @abstractmethod
    async def create(self, spec: TeamSpec) -> TeamSpec: ...

    @abstractmethod
    async def update(self, name: str, updates: dict[str, Any]) -> TeamSpec: ...

    @abstractmethod
    async def delete(self, name: str) -> bool: ...


class FileTeamStore(TeamStore):
    """JSON file-backed team store, same pattern as FileAgentStore."""

    def __init__(self, path: str = "teams.json") -> None:
        self._path = Path(path)
        self._teams: dict[str, TeamSpec] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text())
            for name, raw in data.items():
                self._teams[name] = TeamSpec(**raw)

    def _save(self) -> None:
        data = {name: spec.model_dump() for name, spec in self._teams.items()}
        self._path.write_text(json.dumps(data, indent=2, default=str))

    def seed(self, specs: dict[str, dict[str, Any]]) -> None:
        """Seed teams from YAML config. Overwrites if changed."""
        changed = False
        for name, raw in specs.items():
            raw["name"] = name
            new_spec = TeamSpec(**raw)
            existing = self._teams.get(name)
            if existing is None or existing != new_spec:
                self._teams[name] = new_spec
                changed = True
                logger.info("Seeded team '%s'", name)
        if changed:
            self._save()

    async def get(self, name: str) -> TeamSpec | None:
        return self._teams.get(name)

    async def list(self) -> list[TeamSpec]:
        return list(self._teams.values())

    async def create(self, spec: TeamSpec) -> TeamSpec:
        self._teams[spec.name] = spec
        self._save()
        return spec

    async def update(self, name: str, updates: dict[str, Any]) -> TeamSpec:
        existing = self._teams.get(name)
        if existing is None:
            raise KeyError(f"Team '{name}' not found")
        merged = existing.model_dump()
        merged.update(updates)
        updated = TeamSpec(**merged)
        self._teams[name] = updated
        self._save()
        return updated

    async def delete(self, name: str) -> bool:
        if name not in self._teams:
            return False
        del self._teams[name]
        self._save()
        return True
