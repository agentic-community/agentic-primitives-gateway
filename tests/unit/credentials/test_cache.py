"""Tests for credential cache."""

from __future__ import annotations

import time
from unittest.mock import patch

from agentic_primitives_gateway.credentials.cache import CredentialCache
from agentic_primitives_gateway.credentials.models import ResolvedCredentials


class TestCredentialCache:
    def test_put_and_get(self):
        cache = CredentialCache(ttl_seconds=60)
        creds = ResolvedCredentials(service_credentials={"langfuse": {"key": "val"}})
        cache.put("user1", creds)
        result = cache.get("user1")
        assert result is not None
        assert result.service_credentials["langfuse"]["key"] == "val"

    def test_get_missing(self):
        cache = CredentialCache()
        assert cache.get("nonexistent") is None

    def test_expiry(self):
        cache = CredentialCache(ttl_seconds=1)
        creds = ResolvedCredentials()
        cache.put("user1", creds)
        assert cache.get("user1") is not None

        # Simulate time passing
        with patch.object(time, "monotonic", return_value=time.monotonic() + 2):
            assert cache.get("user1") is None

    def test_lru_eviction(self):
        cache = CredentialCache(ttl_seconds=60, max_entries=2)
        cache.put("user1", ResolvedCredentials())
        cache.put("user2", ResolvedCredentials())
        cache.put("user3", ResolvedCredentials())

        # user1 should have been evicted
        assert cache.get("user1") is None
        assert cache.get("user2") is not None
        assert cache.get("user3") is not None

    def test_lru_access_updates_order(self):
        cache = CredentialCache(ttl_seconds=60, max_entries=2)
        cache.put("user1", ResolvedCredentials())
        cache.put("user2", ResolvedCredentials())

        # Access user1 to make it more recent
        cache.get("user1")

        # Adding user3 should evict user2 (least recently used)
        cache.put("user3", ResolvedCredentials())
        assert cache.get("user1") is not None
        assert cache.get("user2") is None
        assert cache.get("user3") is not None

    def test_invalidate(self):
        cache = CredentialCache()
        cache.put("user1", ResolvedCredentials())
        cache.invalidate("user1")
        assert cache.get("user1") is None

    def test_invalidate_nonexistent(self):
        cache = CredentialCache()
        cache.invalidate("nonexistent")  # Should not raise

    def test_clear(self):
        cache = CredentialCache()
        cache.put("user1", ResolvedCredentials())
        cache.put("user2", ResolvedCredentials())
        cache.clear()
        assert cache.get("user1") is None
        assert cache.get("user2") is None
