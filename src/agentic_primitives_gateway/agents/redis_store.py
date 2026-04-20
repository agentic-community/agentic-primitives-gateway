"""Back-compat shim.

The Redis implementations now live in
:mod:`agentic_primitives_gateway.agents.versioned_agent_store` and
:mod:`agentic_primitives_gateway.agents.versioned_team_store`.
"""

from __future__ import annotations

from agentic_primitives_gateway.agents.versioned_agent_store import (
    RedisVersionedAgentStore as RedisAgentStore,
)
from agentic_primitives_gateway.agents.versioned_team_store import (
    RedisVersionedTeamStore as RedisTeamStore,
)

__all__ = ["RedisAgentStore", "RedisTeamStore"]
