# AgentCore Knowledge Bases

Managed RAG via AWS Bedrock Knowledge Bases.  Retrieval uses
`bedrock-agent-runtime.retrieve`; native retrieve-and-generate uses
`bedrock-agent-runtime.retrieve_and_generate`.

## Configuration

```yaml
providers:
  knowledge:
    backend: "agentic_primitives_gateway.primitives.knowledge.agentcore.AgentCoreKnowledgeProvider"
    config:
      region: "us-east-1"
      knowledge_base_id: "${AGENTCORE_KB_ID:=}"           # per-request via X-Cred-Agentcore-Knowledgebase-Id
      data_source_id: "${AGENTCORE_KB_DATA_SOURCE_ID:=}"   # optional — enables ingest() to trigger a sync
      default_model_arn: "${AGENTCORE_KB_MODEL_ARN:=}"     # required for query()
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `region` | `us-east-1` | AWS region |
| `knowledge_base_id` | – | KB to retrieve against. Resolved per-request from the `X-Cred-Agentcore-Knowledgebase-Id` header when present. |
| `data_source_id` | – | KB data source ID; required to trigger `ingest()` (which starts an ingestion job, not a doc upload). |
| `default_model_arn` | – | Foundation model ARN used by `query()` (native retrieve-and-generate). |

## Supported operations

| Method | Status |
|--------|--------|
| `retrieve` | Supported via `bedrock-agent-runtime.retrieve`. Metadata `source` is populated from `s3Location.uri`. |
| `query` | Supported via `retrieve_and_generate`. **Bypasses `registry.llm`** — the KB owns the model; token accounting for this path is handled by AWS, not the gateway. |
| `ingest` | Starts a data-source sync job. Upload documents to the backing store (e.g. S3) separately. |
| `delete`, `list_documents` | Not implemented. AgentCore KBs don't expose per-document delete / list via the runtime API — delete from the backing store and re-sync. |

## Install

```bash
pip install 'agentic-primitives-gateway[agentcore]'
```

## Example

```bash
# Retrieve (reads knowledge_base_id from header for multi-tenant).
curl -X POST http://localhost:8000/api/v1/knowledge/demo/retrieve \
  -H 'Content-Type: application/json' \
  -H 'X-Cred-Agentcore-Knowledgebase-Id: ABCDEF1234' \
  -d '{"query":"What does our Q4 report say?", "top_k": 5}'

# Native retrieve-and-generate.
curl -X POST http://localhost:8000/api/v1/knowledge/demo/query \
  -H 'Content-Type: application/json' \
  -H 'X-Cred-Agentcore-Knowledgebase-Id: ABCDEF1234' \
  -d '{"question":"What were the top risks in Q4?"}'
```
