"""Generic base classes for spec stores (agent, team, etc.).

Provides ``SpecStore`` (ABC), ``FileSpecStore`` (JSON file-backed), and
``RedisSpecStore`` (Redis hash-backed) that capture the shared CRUD, seeding,
and access-control logic.  Concrete agent/team stores inherit from these and
supply only the spec type and storage-location defaults.
"""

from __future__ import annotations

import builtins
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from agentic_primitives_gateway.auth.access import check_access
from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class SpecStore(ABC, Generic[T]):
    """Abstract base for spec persistence (agents, teams, etc.)."""

    @abstractmethod
    async def get(self, name: str) -> T | None: ...

    @abstractmethod
    async def list(self) -> builtins.list[T]: ...

    async def list_for_user(self, principal: AuthenticatedPrincipal) -> builtins.list[T]:
        """List specs accessible to the given principal.

        Default implementation loads all specs and filters by ownership/groups.
        Backends may override for more efficient filtering.
        """
        all_specs = await self.list()
        return [s for s in all_specs if check_access(principal, s.owner_id, s.shared_with)]  # type: ignore[attr-defined]

    @abstractmethod
    async def create(self, spec: T) -> T: ...

    @abstractmethod
    async def update(self, name: str, updates: dict[str, Any]) -> T: ...

    @abstractmethod
    async def delete(self, name: str) -> bool: ...

    def create_background_run_manager(self, **kwargs: Any) -> Any:
        """Create a BackgroundRunManager with this store's event persistence.

        Override in backends that support cross-replica event stores (e.g. Redis).
        Returns None if no special manager is needed (in-memory default is used).
        """
        return None

    def create_session_registry(self) -> Any:
        """Create a SessionRegistry for this backend.

        Override in backends that support cross-replica session tracking.
        Returns None if no registry is needed (in-memory default is used).
        """
        return None


# ---------------------------------------------------------------------------
# JSON-file-backed implementation
# ---------------------------------------------------------------------------


class FileSpecStore(SpecStore[T]):
    """JSON-file-backed spec store.

    Loads from disk on init, writes on every mutation.
    Supports seeding from config without overwriting unchanged specs.

    Subclasses must set ``_spec_cls`` to the Pydantic model class.
    """

    _spec_cls: type[T]

    def __init__(self, path: str, entity_label: str = "spec") -> None:
        self._path = Path(path)
        self._entity_label = entity_label
        self._specs: dict[str, T] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                for name, spec_dict in data.items():
                    self._specs[name] = self._spec_cls(**spec_dict)
                logger.info(
                    "Loaded %d %s(s) from %s",
                    len(self._specs),
                    self._entity_label,
                    self._path,
                )
            except Exception:
                logger.exception("Failed to load %s(s) from %s", self._entity_label, self._path)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {name: spec.model_dump() for name, spec in self._specs.items()}  # type: ignore[union-attr]
        self._path.write_text(json.dumps(data, indent=2, default=str))

    def seed(self, specs: dict[str, dict[str, Any]]) -> None:
        """Seed specs from YAML config. Overwrites if changed.

        Config-seeded specs default to ``shared_with: ["*"]`` (accessible
        to all authenticated users) unless the config explicitly sets it.
        """
        count = 0
        for name, spec_dict in specs.items():
            spec_dict.setdefault("shared_with", ["*"])
            spec_dict.setdefault("checkpointing_enabled", True)
            new_spec = self._spec_cls(name=name, **spec_dict)
            existing = self._specs.get(name)
            if existing is None or existing != new_spec:
                self._specs[name] = new_spec
                count += 1
                logger.info("Seeded %s '%s'", self._entity_label, name)
        if count:
            self._save()
            logger.info("Seeded/updated %d %s(s) from config", count, self._entity_label)

    async def get(self, name: str) -> T | None:
        return self._specs.get(name)

    async def list(self) -> builtins.list[T]:
        return builtins.list(self._specs.values())

    async def create(self, spec: T) -> T:
        name = spec.name  # type: ignore[attr-defined]
        if name in self._specs:
            raise KeyError(f"{self._entity_label.capitalize()} already exists: {name}")
        self._specs[name] = spec
        self._save()
        return spec

    async def update(self, name: str, updates: dict[str, Any]) -> T:
        existing = self._specs.get(name)
        if existing is None:
            raise KeyError(f"{self._entity_label.capitalize()} not found: {name}")
        updated_data = existing.model_dump()  # type: ignore[union-attr]
        updated_data.update(updates)
        updated_spec = self._spec_cls(**updated_data)
        self._specs[name] = updated_spec
        self._save()
        return updated_spec

    async def delete(self, name: str) -> bool:
        if name in self._specs:
            del self._specs[name]
            self._save()
            return True
        return False


# ---------------------------------------------------------------------------
# Redis-backed implementation
# ---------------------------------------------------------------------------


class RedisSpecStore(SpecStore[T]):
    """Redis hash-backed spec store.

    Stores all specs in a single Redis hash.  Each field is a spec name,
    each value is the JSON-serialized spec.

    Subclasses must set ``_spec_cls`` and ``_redis_key``.
    """

    _spec_cls: type[T]
    _redis_key: str

    def __init__(self, redis_url: str = "redis://localhost:6379/0", entity_label: str = "spec") -> None:
        self._redis = _get_redis(redis_url)
        self._redis_url = redis_url
        self._entity_label = entity_label
        logger.info(
            "%s initialized (url=%s)",
            type(self).__name__,
            redis_url.split("@")[-1],
        )

    def create_background_run_manager(self, **kwargs: Any) -> Any:
        from agentic_primitives_gateway.routes._background import BackgroundRunManager, RedisEventStore

        return BackgroundRunManager(event_store=RedisEventStore(self._redis_url), **kwargs)

    def create_session_registry(self) -> Any:
        from agentic_primitives_gateway.agents.session_registry import RedisSessionRegistry

        return RedisSessionRegistry(redis_url=self._redis_url)

    def seed(self, specs: dict[str, dict[str, Any]]) -> None:
        """Seed specs from config. Runs synchronously at startup via asyncio.

        Config-seeded specs default to ``shared_with: ["*"]`` unless
        the config explicitly sets it.
        """
        import asyncio

        spec_cls = self._spec_cls
        redis_key = self._redis_key
        redis = self._redis
        entity_label = self._entity_label

        async def _seed() -> None:
            count = 0
            for name, spec_dict in specs.items():
                spec_dict.setdefault("shared_with", ["*"])
                spec_dict.setdefault("checkpointing_enabled", True)
                new_spec = spec_cls(name=name, **spec_dict)
                existing_raw = await redis.hget(redis_key, name)
                if existing_raw is None or spec_cls(**json.loads(existing_raw)) != new_spec:
                    await redis.hset(redis_key, name, json.dumps(new_spec.model_dump(), default=str))
                    count += 1
            if count:
                logger.info("Seeded/updated %d %s(s) in Redis", count, entity_label)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_seed())  # noqa: RUF006
        except RuntimeError:
            asyncio.run(_seed())

    async def get(self, name: str) -> T | None:
        raw = await self._redis.hget(self._redis_key, name)
        if raw is None:
            return None
        return self._spec_cls(**json.loads(raw))

    async def list(self) -> builtins.list[T]:
        all_raw = await self._redis.hgetall(self._redis_key)
        return [self._spec_cls(**json.loads(v)) for v in all_raw.values()]

    async def create(self, spec: T) -> T:
        created = await self._redis.hsetnx(
            self._redis_key,
            spec.name,  # type: ignore[attr-defined]
            json.dumps(spec.model_dump(), default=str),  # type: ignore[union-attr]
        )
        if not created:
            raise KeyError(f"{self._entity_label.capitalize()} already exists: {spec.name}")  # type: ignore[attr-defined]
        return spec

    async def update(self, name: str, updates: dict[str, Any]) -> T:
        raw = await self._redis.hget(self._redis_key, name)
        if raw is None:
            raise KeyError(f"{self._entity_label.capitalize()} not found: {name}")
        existing = json.loads(raw)
        existing.update(updates)
        updated = self._spec_cls(**existing)
        await self._redis.hset(
            self._redis_key,
            name,
            json.dumps(updated.model_dump(), default=str),  # type: ignore[union-attr]
        )
        return updated

    async def delete(self, name: str) -> bool:
        return bool(await self._redis.hdel(self._redis_key, name))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_redis(url: str) -> Any:
    """Create an async Redis client."""
    import redis.asyncio as aioredis

    return aioredis.from_url(url, decode_responses=True)
