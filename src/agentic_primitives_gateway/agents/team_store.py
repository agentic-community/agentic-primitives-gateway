from __future__ import annotations

from agentic_primitives_gateway.agents.base_store import FileSpecStore, SpecStore
from agentic_primitives_gateway.models.teams import TeamSpec


class TeamStore(SpecStore[TeamSpec]):
    """Abstract base class for team persistence."""


class FileTeamStore(TeamStore, FileSpecStore[TeamSpec]):
    """JSON file-backed team store, same pattern as FileAgentStore."""

    _spec_cls = TeamSpec

    def __init__(self, path: str = "teams.json") -> None:
        FileSpecStore.__init__(self, path=path, entity_label="team")
