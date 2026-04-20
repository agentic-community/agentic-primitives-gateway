"""Admin-only endpoints for pending agent/team version proposals.

These routes are ``admin``-scoped by default; they exist as a cross-namespace
review queue when ``governance.require_admin_approval_for_deploy`` is on.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from agentic_primitives_gateway.models.agents import AgentVersionListResponse
from agentic_primitives_gateway.models.teams import TeamVersionListResponse
from agentic_primitives_gateway.routes._helpers import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.get("/agents/proposals", response_model=AgentVersionListResponse)
async def list_agent_proposals() -> AgentVersionListResponse:
    """Admin view of pending agent-version proposals across all namespaces."""
    from agentic_primitives_gateway.routes.agents import _get_store

    require_admin()
    store = _get_store()
    versions = await store.list_pending_proposals()
    return AgentVersionListResponse(versions=versions)


@router.get("/teams/proposals", response_model=TeamVersionListResponse)
async def list_team_proposals() -> TeamVersionListResponse:
    """Admin view of pending team-version proposals across all namespaces."""
    from agentic_primitives_gateway.routes.teams import _get_store

    require_admin()
    store = _get_store()
    versions = await store.list_pending_proposals()
    return TeamVersionListResponse(versions=versions)
