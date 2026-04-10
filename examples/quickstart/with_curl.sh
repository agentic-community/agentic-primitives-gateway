#!/usr/bin/env bash
# Quickstart: Interact with the gateway using just curl.
# No SDK needed — it's a REST API.
#
# Prerequisites:
#   Gateway running at localhost:8000 (./run.sh)

BASE=http://localhost:8000

echo "=== Health check ==="
curl -s $BASE/healthz | python3 -m json.tool

echo -e "\n=== List providers ==="
curl -s $BASE/api/v1/providers | python3 -m json.tool

echo -e "\n=== Store a memory ==="
curl -s -X POST $BASE/api/v1/memory/my-namespace \
  -H "Content-Type: application/json" \
  -d '{"key": "favorite-color", "content": "The user likes blue."}' \
  | python3 -m json.tool

echo -e "\n=== Retrieve the memory ==="
curl -s $BASE/api/v1/memory/my-namespace/favorite-color | python3 -m json.tool

echo -e "\n=== Search memory ==="
curl -s -X POST $BASE/api/v1/memory/my-namespace/search \
  -H "Content-Type: application/json" \
  -d '{"query": "what color", "top_k": 3}' \
  | python3 -m json.tool

echo -e "\n=== List agents ==="
curl -s $BASE/api/v1/agents | python3 -m json.tool

echo -e "\n=== Chat with the assistant ==="
curl -s -X POST $BASE/api/v1/agents/assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello! Remember that my name is Alice."}' \
  | python3 -m json.tool

echo -e "\n=== Chat again (agent remembers) ==="
curl -s -X POST $BASE/api/v1/agents/assistant/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is my name?"}' \
  | python3 -m json.tool

echo -e "\n=== LLM completions ==="
curl -s -X POST $BASE/api/v1/llm/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "us.anthropic.claude-sonnet-4-20250514-v1:0",
    "messages": [{"role": "user", "content": "What is 2+2?"}]
  }' | python3 -m json.tool
