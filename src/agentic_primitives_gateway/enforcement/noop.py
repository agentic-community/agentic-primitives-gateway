from __future__ import annotations

import logging
from typing import Any

from agentic_primitives_gateway.enforcement.base import PolicyEnforcer

logger = logging.getLogger(__name__)


class NoopPolicyEnforcer(PolicyEnforcer):
    """No-op enforcer — all requests are allowed.

    This is the default when no enforcement is configured.
    """

    def __init__(self, **kwargs: Any) -> None:
        logger.info("NoopPolicyEnforcer initialized (all requests allowed)")

    async def authorize(
        self,
        principal: str,
        action: str,
        resource: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        return True
