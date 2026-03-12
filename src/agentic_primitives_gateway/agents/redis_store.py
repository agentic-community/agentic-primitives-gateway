"""Redis-backed agent and team stores for multi-replica deployments.

Stores agent/team specs as JSON hashes in Redis. Supports the same seed()
interface as the file-based stores for config-driven initialization.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agentic_primitives_gateway.agents.store import AgentStore
from agentic_primitives_gateway.agents.team_store import TeamStore
from agentic_primitives_gateway.models.agents import AgentSpec
from agentic_primitives_gateway.models.teams import TeamSpec

logger = logging.getLogger(__name__)

_AGENT_KEY = "gateway:agents"
_TEAM_KEY = "gateway:teams"


def _get_redis(url: str) -> Any:
    """Create an async Redis client."""
    import redis.asyncio as aioredis

    return aioredis.from_url(url, decode_responses=True)


class RedisAgentStore(AgentStore):
    """Redis-backed agent spec persistence.

    Stores all agents in a single Redis hash (``gateway:agents``).
    Each field is an agent name, each value is the JSON-serialized spec.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis = _get_redis(redis_url)
        self._redis_url = redis_url
        logger.info("RedisAgentStore initialized (url=%s)", redis_url.split("@")[-1])

    def create_background_run_manager(self, **kwargs: Any) -> Any:
        from agentic_primitives_gateway.routes._background import BackgroundRunManager, RedisEventStore

        return BackgroundRunManager(event_store=RedisEventStore(self._redis_url), **kwargs)

    def create_session_registry(self) -> Any:
        from agentic_primitives_gateway.agents.session_registry import RedisSessionRegistry

        return RedisSessionRegistry(redis_url=self._redis_url)

    def create_checkpoint_store(self) -> Any:
        from agentic_primitives_gateway.agents.checkpoint import RedisCheckpointStore

        return RedisCheckpointStore(redis_url=self._redis_url)

    def seed(self, specs: dict[str, dict[str, Any]]) -> None:
        """Seed agents from config. Runs synchronously at startup via asyncio.

        Config-seeded agents default to ``shared_with: ["*"]`` unless
        the config explicitly sets it.
        """
        import asyncio

        async def _seed() -> None:
            count = 0
            for name, spec_dict in specs.items():
                spec_dict.setdefault("shared_with", ["*"])
                new_spec = AgentSpec(name=name, **spec_dict)
                existing_raw = await self._redis.hget(_AGENT_KEY, name)
                if existing_raw is None or AgentSpec(**json.loads(existing_raw)) != new_spec:
                    await self._redis.hset(_AGENT_KEY, name, json.dumps(new_spec.model_dump(), default=str))
                    count += 1
            if count:
                logger.info("Seeded/updated %d agents in Redis", count)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_seed())  # noqa: RUF006
        except RuntimeError:
            asyncio.run(_seed())

    async def get(self, name: str) -> AgentSpec | None:
        raw = await self._redis.hget(_AGENT_KEY, name)
        if raw is None:
            return None
        return AgentSpec(**json.loads(raw))

    async def list(self) -> list[AgentSpec]:
        all_raw = await self._redis.hgetall(_AGENT_KEY)
        return [AgentSpec(**json.loads(v)) for v in all_raw.values()]

    async def create(self, spec: AgentSpec) -> AgentSpec:
        await self._redis.hset(_AGENT_KEY, spec.name, json.dumps(spec.model_dump(), default=str))
        return spec

    async def update(self, name: str, updates: dict[str, Any]) -> AgentSpec:
        raw = await self._redis.hget(_AGENT_KEY, name)
        if raw is None:
            raise KeyError(f"Agent not found: {name}")
        existing = json.loads(raw)
        existing.update(updates)
        updated = AgentSpec(**existing)
        await self._redis.hset(_AGENT_KEY, name, json.dumps(updated.model_dump(), default=str))
        return updated

    async def delete(self, name: str) -> bool:
        return bool(await self._redis.hdel(_AGENT_KEY, name))


class RedisTeamStore(TeamStore):
    """Redis-backed team spec persistence.

    Same pattern as RedisAgentStore but for team specs.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis = _get_redis(redis_url)
        self._redis_url = redis_url
        logger.info("RedisTeamStore initialized (url=%s)", redis_url.split("@")[-1])

    def create_background_run_manager(self, **kwargs: Any) -> Any:
        from agentic_primitives_gateway.routes._background import BackgroundRunManager, RedisEventStore

        return BackgroundRunManager(event_store=RedisEventStore(self._redis_url), **kwargs)

    def create_session_registry(self) -> Any:
        from agentic_primitives_gateway.agents.session_registry import RedisSessionRegistry

        return RedisSessionRegistry(redis_url=self._redis_url)

    def seed(self, specs: dict[str, dict[str, Any]]) -> None:
        import asyncio

        async def _seed() -> None:
            count = 0
            for name, spec_dict in specs.items():
                spec_dict.setdefault("shared_with", ["*"])
                new_spec = TeamSpec(name=name, **spec_dict)
                existing_raw = await self._redis.hget(_TEAM_KEY, name)
                if existing_raw is None or TeamSpec(**json.loads(existing_raw)) != new_spec:
                    await self._redis.hset(_TEAM_KEY, name, json.dumps(new_spec.model_dump(), default=str))
                    count += 1
            if count:
                logger.info("Seeded/updated %d teams in Redis", count)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_seed())  # noqa: RUF006
        except RuntimeError:
            asyncio.run(_seed())

    async def get(self, name: str) -> TeamSpec | None:
        raw = await self._redis.hget(_TEAM_KEY, name)
        if raw is None:
            return None
        return TeamSpec(**json.loads(raw))

    async def list(self) -> list[TeamSpec]:
        all_raw = await self._redis.hgetall(_TEAM_KEY)
        return [TeamSpec(**json.loads(v)) for v in all_raw.values()]

    async def create(self, spec: TeamSpec) -> TeamSpec:
        await self._redis.hset(_TEAM_KEY, spec.name, json.dumps(spec.model_dump(), default=str))
        return spec

    async def update(self, name: str, updates: dict[str, Any]) -> TeamSpec:
        raw = await self._redis.hget(_TEAM_KEY, name)
        if raw is None:
            raise KeyError(f"Team '{name}' not found")
        existing = json.loads(raw)
        existing.update(updates)
        updated = TeamSpec(**existing)
        await self._redis.hset(_TEAM_KEY, name, json.dumps(updated.model_dump(), default=str))
        return updated

    async def delete(self, name: str) -> bool:
        return bool(await self._redis.hdel(_TEAM_KEY, name))
