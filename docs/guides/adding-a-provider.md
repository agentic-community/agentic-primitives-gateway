# Adding a Provider

How to add a new backend implementation for any primitive.

## Checklist

1. Provider implementation
2. Unit tests
3. Integration tests
4. `pyproject.toml` optional deps
5. Config YAML entry
6. Documentation updates

## Step 1: Implement the Provider

Create a new file in the primitive's directory:

```python
# src/agentic_primitives_gateway/primitives/memory/my_provider.py

from __future__ import annotations
from typing import Any
from agentic_primitives_gateway.primitives.memory.base import MemoryProvider
from agentic_primitives_gateway.models.memory import MemoryRecord, SearchResult

class MyMemoryProvider(MemoryProvider):
    """Memory provider backed by MyService."""

    def __init__(self, api_url: str = "http://localhost:9000", **kwargs: Any) -> None:
        self._url = api_url

    async def store(self, namespace: str, key: str, content: str,
                    metadata: dict[str, Any] | None = None) -> MemoryRecord:
        # Your implementation here
        ...

    async def retrieve(self, namespace: str, key: str) -> MemoryRecord | None:
        ...

    async def search(self, namespace: str, query: str,
                     top_k: int = 10, filters: dict[str, Any] | None = None) -> list[SearchResult]:
        ...

    async def delete(self, namespace: str, key: str) -> bool:
        ...

    async def list_memories(self, namespace: str, filters: dict[str, Any] | None = None,
                            limit: int = 100, offset: int = 0) -> list[MemoryRecord]:
        ...

    async def healthcheck(self) -> bool:
        # Return True if the backend is reachable
        ...
```

!!! tip "Use SyncRunnerMixin for synchronous clients"
    If your backend has a synchronous SDK (like boto3), inherit from `SyncRunnerMixin`:
    ```python
    from agentic_primitives_gateway.primitives._sync import SyncRunnerMixin

    class MyProvider(SyncRunnerMixin, MemoryProvider):
        async def store(self, ...):
            result = await self._run_sync(self._client.put_item, ...)
            return result
    ```

## Step 2: Register in Config

```yaml
providers:
  memory:
    default: "in_memory"
    backends:
      in_memory:
        backend: "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"
        config: {}
      my_provider:
        backend: "agentic_primitives_gateway.primitives.memory.my_provider.MyMemoryProvider"
        config:
          api_url: "http://my-service:9000"
```

The `backend` value is a fully-qualified dotted path. The `config` dict is passed as `**kwargs` to `__init__`.

## Step 3: Write Tests

```python
# tests/test_memory_my_provider.py

import pytest
from agentic_primitives_gateway.primitives.memory.my_provider import MyMemoryProvider

@pytest.fixture
def provider():
    return MyMemoryProvider(api_url="http://test:9000")

class TestMyMemoryProvider:
    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, provider):
        record = await provider.store("ns", "key1", "hello")
        assert record.content == "hello"

        retrieved = await provider.retrieve("ns", "key1")
        assert retrieved is not None
        assert retrieved.content == "hello"
```

## Step 4: Add to kitchen-sink.yaml

```yaml
# configs/kitchen-sink.yaml
providers:
  memory:
    backends:
      my_provider:
        backend: "agentic_primitives_gateway.primitives.memory.my_provider.MyMemoryProvider"
        config:
          api_url: "${MY_SERVICE_URL:=http://localhost:9000}"
```

## What You Get for Free

By implementing the ABC, your provider automatically gets:

- **Prometheus metrics** via MetricsProxy (request count, latency, errors)
- **Per-request routing** via `X-Provider-Memory: my_provider` header
- **Policy enforcement** via Cedar (no changes needed)
- **Agent tool integration** (agents can use your provider's tools)
- **Health checks** in `/readyz`
- **OpenAPI documentation** via FastAPI routes
