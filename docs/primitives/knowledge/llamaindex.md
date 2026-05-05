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
| `llm.backend_name` | `providers.llm.default` | Gateway LLM backend name to pin for `query()` synthesis.  Optional — falls back to the LLM primitive's operator-declared default. |
| `llm.model` | – | Model string forwarded to the resolved LLM backend's `route_request`. |

## LLM routing through the gateway

`query()` (retrieve-and-generate) routes its synthesis call through the gateway's LLM primitive via the `GatewayLlamaLLM` adapter in `primitives/knowledge/_llama_llm_bridge.py`.  Synthesis therefore inherits per-user OIDC-resolved credentials, LLM audit events (`llm.generate`), and token accounting (`gateway_llm_tokens_total`).

**Synthesis LLM selection is operator-scope.**  The bridge resolves the synthesis backend in this order and explicitly bypasses the request-scoped `X-Provider-Llm` contextvar:

1. `llm.backend_name` on this knowledge config, if set.
2. `providers.llm.default` — the LLM primitive's operator-declared default.

That matches LlamaIndex's own idiom of `llm or Settings.llm` (see `RetrieverQueryEngine`, `as_query_engine`), where the gateway's `providers.llm.default` plays the role of `Settings.llm`.  Callers cannot redirect RAG synthesis via `X-Provider-Llm` — that header routes *caller-facing* LLM calls (chat completions, tool calls).  Routing synthesis per-request would let callers silently change which LLM handles an operator-configured RAG path.

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

## Structured citations

When `retrieve()` is called with `include_citations=True` (REST: `{"include_citations": true}` in the body; agent tool: `search_knowledge(..., include_sources=true)`), each returned chunk carries a `citations: list[Citation]` populated from LlamaIndex node metadata:

| Citation field | Source |
|----------------|--------|
| `source` | `_apg_source` marker, or `metadata.source`, or `metadata.file_path`, or `metadata.file_name` |
| `uri` | `metadata.url` or `metadata.uri` when present |
| `page` | `metadata.page_label` or `metadata.page_number` (common for PDF readers) |
| `span` | `(node.start_char_idx, node.end_char_idx)` when LlamaIndex populated them during node parsing |
| `snippet` | First 200 chars of the chunk text |
| `metadata` | Remaining node metadata, with the fields above and internal `_apg_*` markers stripped |

Default behaviour (flag off) leaves `chunk.citations = None` — the common path stays compact.

## Observability

Knowledge-specific metrics are emitted automatically (labels bounded by provider/store_type taxonomy):

- `gateway_knowledge_chunks_retrieved_total`
- `gateway_knowledge_retrieval_score` (histogram of top-1 scores)
- `gateway_knowledge_documents_ingested_total`
- `gateway_knowledge_query_tokens_total` (when synthesis tokens are surfaced)

Audit events: `knowledge.ingest`, `knowledge.retrieve`, `knowledge.query`, `knowledge.delete` — each carries `chunk_count`, `top_score`, `document_count` in `metadata` where relevant.
