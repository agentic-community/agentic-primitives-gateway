"""Back-compat shim.

The real implementation now lives in
:mod:`agentic_primitives_gateway.agents.versioned_team_store`.
"""

from __future__ import annotations

from agentic_primitives_gateway.agents.versioned_team_store import (
    FileVersionedTeamStore as FileTeamStore,
)
from agentic_primitives_gateway.agents.versioned_team_store import (
    VersionedTeamStore as TeamStore,
)

__all__ = ["FileTeamStore", "TeamStore"]
