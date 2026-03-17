"""Redis-backed task board provider for multi-replica deployments.

Each team run's tasks are stored as a Redis hash: ``tasks:{team_run_id}``.
Fields are task IDs, values are JSON-serialized Task objects.

Atomic claiming uses a Lua script to check status + dependencies and set
``claimed`` in a single round-trip — no distributed locks needed.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from agentic_primitives_gateway.models.tasks import Task, TaskNote
from agentic_primitives_gateway.primitives.tasks.base import TasksProvider

logger = logging.getLogger(__name__)


def _task_key(team_run_id: str) -> str:
    return f"tasks:{team_run_id}"


# Lua script for atomic claim: checks status is PENDING and all deps are DONE,
# then sets status to CLAIMED and assigned_to. Returns the updated JSON or nil.
_CLAIM_LUA = """
local key = KEYS[1]
local task_id = ARGV[1]
local agent_name = ARGV[2]
local now = ARGV[3]

local raw = redis.call('HGET', key, task_id)
if not raw then return nil end

local task = cjson.decode(raw)
if task['status'] ~= 'pending' then return nil end

-- Check dependencies
if task['depends_on'] then
    for _, dep_id in ipairs(task['depends_on']) do
        local dep_raw = redis.call('HGET', key, dep_id)
        if not dep_raw then return nil end
        local dep = cjson.decode(dep_raw)
        if dep['status'] ~= 'done' then return nil end
    end
end

task['status'] = 'claimed'
task['assigned_to'] = agent_name
task['updated_at'] = now

local updated = cjson.encode(task)
redis.call('HSET', key, task_id, updated)
return updated
"""


# Lua script for atomic update: merges status/result into the task JSON.
_UPDATE_LUA = """
local key = KEYS[1]
local task_id = ARGV[1]
local new_status = ARGV[2]
local new_result = ARGV[3]
local now = ARGV[4]

local raw = redis.call('HGET', key, task_id)
if not raw then return nil end

local task = cjson.decode(raw)
task['updated_at'] = now
if new_status ~= '' then task['status'] = new_status end
if new_result ~= '' then task['result'] = new_result end

local updated = cjson.encode(task)
redis.call('HSET', key, task_id, updated)
return updated
"""

# Lua script for atomic add_note: appends a note to the task's notes array.
_ADD_NOTE_LUA = """
local key = KEYS[1]
local task_id = ARGV[1]
local note_json = ARGV[2]
local now = ARGV[3]

local raw = redis.call('HGET', key, task_id)
if not raw then return nil end

local task = cjson.decode(raw)
task['updated_at'] = now
if not task['notes'] then task['notes'] = {} end
table.insert(task['notes'], cjson.decode(note_json))

local updated = cjson.encode(task)
redis.call('HSET', key, task_id, updated)
return updated
"""


def _parse_task(raw: str) -> Task:
    """Parse a Task from Redis JSON, fixing Lua cjson empty-array-as-object."""
    data = json.loads(raw)
    # Lua's cjson encodes [] as {} — normalize list fields
    for field in ("depends_on", "notes"):
        if isinstance(data.get(field), dict):
            data[field] = []
    return Task(**data)


class RedisTasksProvider(TasksProvider):
    """Redis-backed task board with atomic Lua-based operations."""

    def __init__(self, redis_url: str = "redis://localhost:6379/0", **kwargs: Any) -> None:
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._redis_url = redis_url
        self._scripts: dict[str, Any] = {}
        logger.info("RedisTasksProvider initialized (url=%s)", redis_url.split("@")[-1])

    async def _script(self, name: str, lua: str) -> Any:
        if name not in self._scripts:
            self._scripts[name] = self._redis.register_script(lua)
        return self._scripts[name]

    async def create_task(
        self,
        team_run_id: str,
        title: str,
        *,
        description: str = "",
        created_by: str = "",
        depends_on: list[str] | None = None,
        priority: int = 0,
        suggested_worker: str | None = None,
    ) -> Task:
        now = datetime.now(UTC)
        task = Task(
            id=uuid.uuid4().hex[:12],
            team_run_id=team_run_id,
            title=title,
            description=description,
            created_by=created_by,
            depends_on=depends_on or [],
            priority=priority,
            suggested_worker=suggested_worker,
            created_at=now,
            updated_at=now,
        )
        await self._redis.hset(_task_key(team_run_id), task.id, task.model_dump_json())
        return task

    async def get_task(self, team_run_id: str, task_id: str) -> Task | None:
        raw = await self._redis.hget(_task_key(team_run_id), task_id)
        if raw is None:
            return None
        return _parse_task(raw)

    async def list_tasks(
        self,
        team_run_id: str,
        *,
        status: str | None = None,
        assigned_to: str | None = None,
    ) -> list[Task]:
        all_raw = await self._redis.hgetall(_task_key(team_run_id))
        tasks = [_parse_task(v) for v in all_raw.values()]
        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        if assigned_to is not None:
            tasks = [t for t in tasks if t.assigned_to == assigned_to]
        return sorted(tasks, key=lambda t: (-t.priority, t.created_at))

    async def claim_task(
        self,
        team_run_id: str,
        task_id: str,
        agent_name: str,
    ) -> Task | None:
        script = await self._script("claim", _CLAIM_LUA)
        now = datetime.now(UTC).isoformat()
        result = await script(keys=[_task_key(team_run_id)], args=[task_id, agent_name, now])
        if result is None:
            return None
        return _parse_task(result)

    async def update_task(
        self,
        team_run_id: str,
        task_id: str,
        *,
        status: str | None = None,
        result: str | None = None,
    ) -> Task | None:
        script = await self._script("update", _UPDATE_LUA)
        now = datetime.now(UTC).isoformat()
        raw = await script(
            keys=[_task_key(team_run_id)],
            args=[task_id, status or "", result or "", now],
        )
        if raw is None:
            return None
        return _parse_task(raw)

    async def add_note(
        self,
        team_run_id: str,
        task_id: str,
        note: TaskNote,
    ) -> Task | None:
        script = await self._script("add_note", _ADD_NOTE_LUA)
        now = datetime.now(UTC).isoformat()
        raw = await script(
            keys=[_task_key(team_run_id)],
            args=[task_id, note.model_dump_json(), now],
        )
        if raw is None:
            return None
        return _parse_task(raw)

    async def healthcheck(self) -> bool:
        """Sync Redis ping — avoids event-loop mismatch when called via asyncio.run() in a thread."""
        import redis

        try:
            r = redis.from_url(self._redis_url, decode_responses=True, socket_timeout=2)
            result = r.ping()
            r.close()
            return bool(result)
        except Exception:
            return False
