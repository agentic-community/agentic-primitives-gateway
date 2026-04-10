from __future__ import annotations

from typing import Any

from agentic_primitives_gateway.agents.base_store import FileSpecStore, SpecStore
from agentic_primitives_gateway.models.agents import AgentSpec


class AgentStore(SpecStore[AgentSpec]):
    """Abstract base for agent spec persistence."""

    def create_checkpoint_store(self) -> Any:
        """Create a CheckpointStore for durable run execution.

        Override in backends that support cross-replica state persistence.
        Returns None if checkpointing is not supported (runs are ephemeral).
        """
        return None


class FileAgentStore(AgentStore, FileSpecStore[AgentSpec]):
    """JSON-file-backed agent store.

    Loads from disk on init, writes on every mutation.
    Supports seeding from config without overwriting existing agents.
    """

    _spec_cls = AgentSpec

    def __init__(self, path: str = "agents.json") -> None:
        FileSpecStore.__init__(self, path=path, entity_label="agent")

    @property
    def _agents(self) -> dict[str, AgentSpec]:
        """Alias for ``_specs``."""
        return self._specs
