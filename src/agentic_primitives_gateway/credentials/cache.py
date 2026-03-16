"""In-memory LRU cache for resolved credentials."""

from __future__ import annotations

import time
from collections import OrderedDict
from threading import Lock

from agentic_primitives_gateway.credentials.models import ResolvedCredentials


class CredentialCache:
    """Thread-safe in-memory LRU cache keyed by user_id.

    Entries expire after ``ttl_seconds`` and the cache evicts the
    least-recently-used entry when ``max_entries`` is reached.
    """

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 10000) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._cache: OrderedDict[str, tuple[ResolvedCredentials, float]] = OrderedDict()
        self._lock = Lock()

    def get(self, user_id: str) -> ResolvedCredentials | None:
        with self._lock:
            entry = self._cache.get(user_id)
            if entry is None:
                return None
            creds, expires_at = entry
            if time.monotonic() > expires_at:
                del self._cache[user_id]
                return None
            self._cache.move_to_end(user_id)
            return creds

    def put(self, user_id: str, creds: ResolvedCredentials) -> None:
        with self._lock:
            expires_at = time.monotonic() + self._ttl
            self._cache[user_id] = (creds, expires_at)
            self._cache.move_to_end(user_id)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)

    def invalidate(self, user_id: str) -> None:
        with self._lock:
            self._cache.pop(user_id, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
