"""Audit integration tests run against a local Redis and do NOT need AWS.

The top-level ``tests/integration/conftest.py`` pins an AgentCore-only
registry with a hard AWS skip — overriding that here lets audit tests
run on any laptop with Redis.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _init_registry():
    """Override the parent conftest's AWS-gated registry fixture with a no-op."""
    yield
