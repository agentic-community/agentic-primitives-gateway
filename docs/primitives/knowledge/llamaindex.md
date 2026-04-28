# LlamaIndex Knowledge Provider

LlamaIndex-backed RAG / graph retrieval with pluggable storage — vector, property-graph, or hybrid GraphRAG — configured through YAML, no new provider class per backend.

## Configuration

```yaml
providers:
  knowledge:
    backend: "agentic_primitives_gateway.primitives.knowledge.llamaindex.LlamaIndexKnowledgeProvider"
    config:
      store_type: vector          # vector | graph | hybrid
      vector_store:                # optional — defaults to SimpleVectorStore (in-memory)
        provider: pinecone         # simple | pinecone | pgvector | milvus | weaviate
        config: {...}              # passed through to the LlamaIndex store
      graph_store:                 # optional — required when store_type is graph or hybrid
        provider: falkordb         # falkordb | neo4j
        config:
          url: "redis://localhost:6379"
          database: "apg_knowledge"
      embed_model:                 # embeddings still use an external model
        provider: bedrock          # bedrock | openai | huggingface
        config:
          model_name: amazon.titan-embed-text-v2:0
      llm:                         # optional — used ONLY by query()
        backend_name: bedrock      # pins a gateway LLM backend
        model: us.anthropic.claude-sonnet-4-20250514-v1:0
        max_tokens: 2048
```

| Key | Default | Description |
|-----|---------|-------------|
| `store_type` | `vector` | `vector` (`VectorStoreIndex`), `graph` (`PropertyGraphIndex`), or `hybrid` (both) |
| `vector_store.provider` | `simple` | `simple` (in-memory), `pinecone`, `pgvector`, `milvus`, `weaviate` |
| `graph_store.provider` | – | `falkordb`, `neo4j` |
| `embed_model.provider` | – | `bedrock`, `openai`, `huggingface` |
| `llm.backend_name` | – | Gateway LLM backend name to pin for `query()` synthesis |
| `llm.model` | – | Model string passed through `registry.llm.route_request` |

## LLM routing through the gateway

`query()` (retrieve-and-generate) routes its synthesis call through `registry.llm` via the `GatewayLlamaLLM` adapter in `primitives/knowledge/_llama_llm_bridge.py`.  This means LlamaIndex's internal completion inherits everything the gateway's LLM primitive already does: provider routing (`X-Provider-Llm`), per-user OIDC-resolved credentials, LLM audit events (`llm.generate`), and token accounting (`gateway_llm_tokens_total`).

**Embeddings still use an external model** — the gateway has no `embeddings` primitive yet, so `embed_model` points directly at Bedrock / OpenAI / HuggingFace.

## Install

```bash
pip install 'agentic-primitives-gateway[knowledge-llamaindex]'
# For the FalkorDB graph store:
pip install 'agentic-primitives-gateway[knowledge-falkordb]'
```

## Quick example

```bash
# Ingest three documents.
curl -X POST http://localhost:8000/api/v1/knowledge/demo/documents \
  -H 'Content-Type: application/json' \
  -d '{"documents":[
    {"text":"The Eiffel Tower is in Paris."},
    {"text":"The Colosseum is in Rome."},
    {"text":"Paris has excellent pastries."}
  ]}'

# Retrieve relevant chunks.
curl -X POST http://localhost:8000/api/v1/knowledge/demo/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is in Paris?", "top_k": 2}'

# Native retrieve-and-generate (routes synthesis through the gateway LLM).
curl -X POST http://localhost:8000/api/v1/knowledge/demo/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is in Paris?"}'
```

## Observability

Knowledge-specific metrics are emitted automatically (labels bounded by provider/store_type taxonomy):

- `gateway_knowledge_chunks_retrieved_total`
- `gateway_knowledge_retrieval_score` (histogram of top-1 scores)
- `gateway_knowledge_documents_ingested_total`
- `gateway_knowledge_query_tokens_total` (when synthesis tokens are surfaced)

Audit events: `knowledge.ingest`, `knowledge.retrieve`, `knowledge.query`, `knowledge.delete` — each carries `chunk_count`, `top_score`, `document_count` in `metadata` where relevant.
