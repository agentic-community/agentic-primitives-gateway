"""Redis-backed persistence mixin for the versioned spec store.

Concrete Redis-backed agent and team stores compose :class:`RedisSpecStore`
with the spec-specific logic in ``store.py`` / ``team_store.py``.

The mixin uses a snapshot read / full rewrite pattern (one pipeline per
save).  Low contention in practice because every mutation already holds
the fully-computed new state; swapping to a WATCH/MULTI per-op layout
is a future optimization.

Key layout (per store — the ``_namespace_prefix`` class attr distinguishes
agent vs team in the same Redis instance):

* ``{prefix}:versions``     hash   version_id → JSON version record
* ``{prefix}:identities``   hash   "owner:name" → JSON identity metadata
* ``{prefix}:proposals``    list   pending proposal keys
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agentic_primitives_gateway.agents.base_store import _StoreState

logger = logging.getLogger(__name__)


class RedisSpecStore:
    """Redis-backed persistence — supplies ``_load_state`` / ``_save_state``.

    Expected attributes on the composed class:

    * ``_entity_label``       — for log messages
    * ``_version_name_field`` — the version model's name field key
    * ``_namespace_prefix``   — Redis key prefix
    """

    _entity_label: str
    _version_name_field: str
    _namespace_prefix: str

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._redis_url = redis_url
        logger.info(
            "%s initialized (url=%s)",
            type(self).__name__,
            redis_url.split("@")[-1],
        )

    async def _load_state(self) -> _StoreState:
        pipe = self._redis.pipeline()
        pipe.hgetall(f"{self._namespace_prefix}:versions")
        pipe.hgetall(f"{self._namespace_prefix}:identities")
        pipe.lrange(f"{self._namespace_prefix}:proposals", 0, -1)
        versions_raw, identities_raw, proposals = await pipe.execute()
        state = _StoreState()
        state.versions = {k: json.loads(v) for k, v in versions_raw.items()}
        state.identities = {k: json.loads(v) for k, v in identities_raw.items()}
        state.proposals = list(proposals)
        return state

    async def _save_state(self, state: _StoreState) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(f"{self._namespace_prefix}:versions")
        if state.versions:
            pipe.hset(
                f"{self._namespace_prefix}:versions",
                mapping={k: json.dumps(v, default=str) for k, v in state.versions.items()},
            )
        pipe.delete(f"{self._namespace_prefix}:identities")
        if state.identities:
            pipe.hset(
                f"{self._namespace_prefix}:identities",
                mapping={k: json.dumps(v, default=str) for k, v in state.identities.items()},
            )
        pipe.delete(f"{self._namespace_prefix}:proposals")
        if state.proposals:
            pipe.rpush(f"{self._namespace_prefix}:proposals", *state.proposals)
        await pipe.execute()

    def create_background_run_manager(self, **kwargs: Any) -> Any:
        from agentic_primitives_gateway.routes._background import (
            BackgroundRunManager,
            RedisEventStore,
        )

        return BackgroundRunManager(event_store=RedisEventStore(self._redis_url), **kwargs)

    def create_session_registry(self) -> Any:
        from agentic_primitives_gateway.agents.session_registry import RedisSessionRegistry

        return RedisSessionRegistry(redis_url=self._redis_url)

    def create_checkpoint_store(self) -> Any:
        from agentic_primitives_gateway.agents.checkpoint import RedisCheckpointStore

        return RedisCheckpointStore(redis_url=self._redis_url)
