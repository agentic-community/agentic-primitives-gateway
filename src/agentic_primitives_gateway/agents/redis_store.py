"""Redis-backed agent and team stores for multi-replica deployments.

Stores agent/team specs as JSON hashes in Redis. Supports the same seed()
interface as the file-based stores for config-driven initialization.
"""

from __future__ import annotations

from typing import Any

from agentic_primitives_gateway.agents.base_store import RedisSpecStore
from agentic_primitives_gateway.agents.store import AgentStore
from agentic_primitives_gateway.agents.team_store import TeamStore
from agentic_primitives_gateway.models.agents import AgentSpec
from agentic_primitives_gateway.models.teams import TeamSpec

_AGENT_KEY = "gateway:agents"
_TEAM_KEY = "gateway:teams"


class RedisAgentStore(AgentStore, RedisSpecStore[AgentSpec]):
    """Redis-backed agent spec persistence.

    Stores all agents in a single Redis hash (``gateway:agents``).
    Each field is an agent name, each value is the JSON-serialized spec.
    """

    _spec_cls = AgentSpec
    _redis_key = _AGENT_KEY

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        RedisSpecStore.__init__(self, redis_url=redis_url, entity_label="agent")

    def create_checkpoint_store(self) -> Any:
        from agentic_primitives_gateway.agents.checkpoint import RedisCheckpointStore

        return RedisCheckpointStore(redis_url=self._redis_url)


class RedisTeamStore(TeamStore, RedisSpecStore[TeamSpec]):
    """Redis-backed team spec persistence.

    Same pattern as RedisAgentStore but for team specs.
    """

    _spec_cls = TeamSpec
    _redis_key = _TEAM_KEY

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        RedisSpecStore.__init__(self, redis_url=redis_url, entity_label="team")
