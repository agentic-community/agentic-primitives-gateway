from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PolicyEnforcer(ABC):
    """Abstract base for policy enforcement backends.

    Implementations evaluate authorization requests against a policy set.
    The enforcer is separate from the ``PolicyProvider`` (CRUD) — it reads
    policies from the store for evaluation at request time.
    """

    @abstractmethod
    async def authorize(
        self,
        principal: str,
        action: str,
        resource: str,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Return ``True`` if the request is allowed, ``False`` otherwise."""
        ...

    async def load_policies(self) -> None:  # noqa: B027
        """Sync policies from the policy store.

        Called by background refresh. Default is a no-op.
        """

    async def close(self) -> None:  # noqa: B027
        """Cleanup resources. Called on shutdown. Default is a no-op."""
