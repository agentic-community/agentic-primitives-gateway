# Mem0 + Milvus

Self-hosted memory provider using [Mem0](https://mem0.ai/) with [Milvus](https://milvus.io/) vector database for semantic search. Full control over your data with production-grade vector retrieval.

## Configuration

### Server-Side (Gateway Config)

```yaml
providers:
  memory:
    backend: "agentic_primitives_gateway.primitives.memory.mem0_provider.Mem0MemoryProvider"
    config:
      vector_store:
        provider: milvus
        config:
          collection_name: agentic_memories
          url: "http://localhost:19530"
          token: ""
          embedding_model_dims: 1024
      llm:
        provider: aws_bedrock
        config:
          model: us.anthropic.claude-sonnet-4-20250514-v1:0
      embedder:
        provider: aws_bedrock
        config:
          model: amazon.titan-embed-text-v2:0
```

| Parameter | Description |
|-----------|-------------|
| `vector_store.provider` | Vector database provider (`milvus`) |
| `vector_store.config.collection_name` | Milvus collection name |
| `vector_store.config.url` | Milvus server URL |
| `vector_store.config.embedding_model_dims` | Embedding dimensions (must match the embedder model) |
| `llm.provider` | LLM provider for Mem0's internal processing (`aws_bedrock`) |
| `llm.config.model` | Model ID for LLM processing |
| `embedder.provider` | Embedding provider (`aws_bedrock`) |
| `embedder.config.model` | Model ID for embeddings |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MILVUS_HOST` | `localhost` | Milvus server hostname |
| `MILVUS_PORT` | `19530` | Milvus server port |

## Running Milvus Locally

```bash
# Using Docker Compose (recommended)
docker compose -f deploy/docker-compose-milvus.yml up -d

# Or standalone
docker run -d --name milvus -p 19530:19530 -p 9091:9091 milvusdb/milvus:latest
```

## Using the Memory API

All standard memory endpoints work identically to other providers:

```bash
# Store (Mem0 automatically generates embeddings)
curl -X POST http://localhost:8000/api/v1/memory/agent:researcher \
  -H "Content-Type: application/json" \
  -d '{"key": "finding-1", "content": "The global AI market is projected to reach $1.8T by 2030"}'

# Semantic search
curl "http://localhost:8000/api/v1/memory/agent:researcher/search?query=AI+market+size&top_k=5"
```

## Using with Declarative Agents

```yaml
agents:
  specs:
    researcher:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      primitives:
        memory:
          enabled: true
          namespace: "agent:{agent_name}"
      provider_overrides:
        memory: "mem0"
      hooks:
        auto_memory: true
```

## Using with the Python Client

```python
from agentic_primitives_gateway_client import AgenticPlatformClient, Memory

client = AgenticPlatformClient("http://localhost:8000", aws_from_environment=True)
memory = Memory(client, namespace="agent:researcher")

await memory.remember("finding-1", "AI market projected to reach $1.8T by 2030")
results = await memory.search("market projections")
```

## How It Works

1. **Store**: Mem0 processes the content, generates embeddings via the configured embedder, and stores the vector + metadata in Milvus
2. **Search**: the query is embedded using the same model, then Milvus performs approximate nearest neighbor (ANN) search
3. **Memory extraction**: Mem0 can automatically extract and organize key facts from longer content using the configured LLM

Store and delete operations serialize the key lookup and mutation inside one gateway process, so concurrent stores of the same `(namespace, key)` do not insert duplicate records there. This lock does **not** coordinate multiple gateway replicas: use a backend-enforced unique key or a distributed lock if your deployment requires cross-replica uniqueness. Delete scans all existing rows for the key (including past Mem0's default 20-result limit) and removes each match, cleaning up duplicates from earlier cross-replica writes. It is not an atomic distributed delete: another replica can write the same key during or after the scan. Non-Bedrock reads are not serialized behind these writes.

## Prerequisites

- `pip install agentic-primitives-gateway[mem0]`
- Running Milvus instance
- AWS credentials for Bedrock (if using Bedrock for LLM/embeddings). Each operation rebinds mem0's Bedrock LLM/embedder clients to the request's AWS session — the caller's `X-AWS-*` / OIDC credentials, or the server chain as `allow_server_credentials` permits (`always` ignores caller keys; `never` requires them). The shared mem0 instance is first built with the requesting caller's credentials (not the process environment); the temporary configuration and both the stored mem0 configuration and component configurations are scrubbed afterwards. The process environment is never modified. Only top-level `llm` / `embedder` with `aws_bedrock` are supported as AWS-backed components; `neptune`, `s3_vectors`, unknown providers and configurable bridge providers are rejected before initialization so unbound components cannot run as the gateway's AWS identity. Anonymous readiness checks Milvus collection availability when Milvus is configured and returns `reachable` without constructing mem0 or using AWS; it is not a Bedrock connectivity check. Other Bedrock-backed vector stores also report `reachable` without a backend probe.
