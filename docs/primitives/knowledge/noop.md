# Noop Knowledge Provider

Zero-configuration stub.  Used as the default when knowledge retrieval
isn't configured, and by tests.  `ingest` accepts documents but reports
`ingested: 0`; `retrieve` / `list_documents` return empty; `delete`
returns `false`.

```yaml
providers:
  knowledge:
    backend: "agentic_primitives_gateway.primitives.knowledge.noop.NoopKnowledgeProvider"
```

Use [LlamaIndex](llamaindex.md) for self-hosted RAG or
[AgentCore](agentcore.md) for AWS Bedrock Knowledge Bases.
