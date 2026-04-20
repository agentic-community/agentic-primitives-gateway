"""Back-compat shim.

The real implementation now lives in
:mod:`agentic_primitives_gateway.agents.versioned_agent_store`.  This module
preserves the old import paths so route handlers and tests continue to work
unchanged while Phase 2 migrates call sites to the versioned API.
"""

from __future__ import annotations

from agentic_primitives_gateway.agents.versioned_agent_store import (
    FileVersionedAgentStore as FileAgentStore,
)
from agentic_primitives_gateway.agents.versioned_agent_store import (
    VersionedAgentStore as AgentStore,
)

__all__ = ["AgentStore", "FileAgentStore"]
