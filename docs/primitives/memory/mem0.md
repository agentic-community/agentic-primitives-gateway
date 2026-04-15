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

## Prerequisites

- `pip install agentic-primitives-gateway[mem0]`
- Running Milvus instance
- AWS credentials for Bedrock (if using Bedrock for LLM/embeddings)
