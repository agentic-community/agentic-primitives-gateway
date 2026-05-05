# Knowledge API

Prefix: `/api/v1/knowledge`

**Backends:** `NoopKnowledgeProvider`, [`LlamaIndexKnowledgeProvider`](../primitives/knowledge/llamaindex.md), [`AgentCoreKnowledgeProvider`](../primitives/knowledge/agentcore.md)

The knowledge primitive unifies RAG (vector) and property-graph
retrieval behind one ABC.  It's distinct from `memory`: memory is the
user-scoped state an agent writes during a run; knowledge is a
bulk-indexed corpus the agent reads from for context.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/{namespace}/documents` | Ingest documents |
| `GET`  | `/{namespace}/documents` | List ingested documents (`limit`, `offset`) |
| `DELETE` | `/{namespace}/documents/{document_id}` | Delete one document |
| `POST` | `/{namespace}/retrieve` | Retrieve ranked chunks (no synthesis) |
| `POST` | `/{namespace}/query` | Native retrieve-and-generate (optional per backend — 501 when unsupported) |
| `GET`  | `/namespaces` | List namespaces visible to the caller |

### Ingest documents

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/support-corpus/documents \
  -H 'Content-Type: application/json' \
  -d '{"documents": [
    {"text": "Our refund policy is 30 days from purchase.", "metadata": {"topic": "refunds"}, "source": "faq.md"},
    {"text": "Shipping is free for orders over $50.", "metadata": {"topic": "shipping"}}
  ]}'
```

Response (201):

```json
{"document_ids": ["ab12...", "cd34..."], "ingested": 2}
```

### Retrieve chunks

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/support-corpus/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"query": "how long do I have to return an item?", "top_k": 3}'
```

Response (200):

```json
{"chunks": [
  {"chunk_id": "…", "document_id": "ab12…", "text": "Our refund policy is 30 days from purchase.", "score": 0.87, "metadata": {"topic": "refunds", "source": "faq.md"}}
]}
```

#### Structured citations

Pass `include_citations: true` to ask the provider for structured source references (`source`, `uri`, `page`, `span`, plus a passthrough `metadata` dict).  Providers that cannot produce citations leave the field `null`.  The default is `false` so the common path stays compact.

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/support-corpus/retrieve \
  -H 'Content-Type: application/json' \
  -d '{"query": "refunds?", "top_k": 2, "include_citations": true}'
```

Each chunk gains a `citations` list when supported:

```json
{"chunks": [
  {
    "chunk_id": "…", "document_id": "ab12…", "text": "Our refund policy is 30 days from purchase.", "score": 0.87,
    "metadata": {"topic": "refunds", "source": "faq.md"},
    "citations": [{"source": "faq.md", "uri": null, "page": "3", "span": [0, 47], "snippet": "Our refund policy…", "metadata": {}}]
  }
]}
```

#### Metadata scrubbing

`RetrievedChunk.metadata` is operator-controlled and flows verbatim to callers (same trust model as `MemoryRecord.metadata`).  Operators who want to strip specific keys before they leave the gateway — e.g. internal bucket identifiers or ingest-pipeline bookkeeping — add them to the single `metadata_denylists` config dict keyed by primitive name:

```yaml
metadata_denylists:
  knowledge: ["internal_ingest_id", "pipeline_stage"]
  memory: ["audit_trail_id"]
```

The denylist is applied uniformly in `primitives/knowledge/_audit.wrap_retrieve`, so REST and the agent `search_knowledge` tool both see the same scrubbed shape.  Top-level keys only — nested structures are not recursed.  Citation metadata is scrubbed with the same list.  The same `metadata_denylists` dict drives scrubbing for every primitive that opts into the pattern (see [Memory API](memory.md#metadata-scrubbing)).

### Native retrieve-and-generate

Optional — only backends that support native synthesis implement this; unsupported backends return **501**.

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/support-corpus/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "how long do I have to return an item?", "top_k": 3}'
```

!!! note
    The canonical pattern in this gateway is **retrieve through knowledge, synthesize through the LLM primitive** — that keeps credentials, audit, and token accounting uniform across every LLM call.  `query()` is a convenience; LlamaIndex backends route their internal synthesis through `registry.llm` so the trade-off is minimal, but AgentCore KBs call their own model directly.

## Agent tool

Enable the knowledge primitive on an agent spec and the LLM gets a `search_knowledge` tool:

```yaml
agents:
  specs:
    support-bot:
      model: us.anthropic.claude-sonnet-4-20250514-v1:0
      system_prompt: "You are a support bot. Ground every answer in the knowledge base."
      primitives:
        knowledge:
          enabled: true
          namespace: "support-corpus"
```

The agent will call `search_knowledge(query, top_k)` and receive scored chunks with source metadata.

### Source citations in the UI

`search_knowledge` accepts an optional `include_sources: true` argument.  When the LLM opts in, the handler attaches structured chunks (text, score, metadata, structured `citations`) to the tool's `ToolArtifact.structured` sideband.  The web UI renders them as collapsible source cards in the tool-call panel.  The text given to the LLM is unchanged — token cost stays the same.

### Inline citation markers

For answers that need per-claim attribution (Perplexity-style), enable `inline_citations` on the agent spec:

```yaml
agents:
  specs:
    support-bot:
      primitives:
        knowledge:
          enabled: true
          namespace: "support-corpus"
          options:
            inline_citations: true
```

The tool output prepends each chunk with a globally-unique `[N]` marker and includes a one-line instruction telling the model to cite claims with those markers.  Multiple `search_knowledge` calls in the same turn use contiguous ranges of indices (no collisions).  The UI rewrites `[N]` in the assistant's streamed tokens into clickable pills linked to the corresponding chunk card.

## Audit + metrics

Every `retrieve` / `query` / `ingest` call emits an audit event (`knowledge.retrieve`, `knowledge.query`, `knowledge.ingest`) with chunk counts and top-1 relevance score in `metadata`.  Prometheus metrics:

- `gateway_knowledge_chunks_retrieved_total{provider, store_type}`
- `gateway_knowledge_retrieval_score{provider, store_type}` (histogram)
- `gateway_knowledge_documents_ingested_total{provider, store_type}`
- `gateway_knowledge_query_tokens_total{provider, kind}`
