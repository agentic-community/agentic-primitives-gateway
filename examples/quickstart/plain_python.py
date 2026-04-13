"""Quickstart: Plain Python agent using the gateway client.

No framework needed — just boto3 for the LLM and the gateway client
for primitives. This is the simplest possible agent.

Prerequisites:
    pip install agentic-primitives-gateway-client[aws]
    # Gateway running at localhost:8000 (./run.sh)

Usage:
    python examples/quickstart/plain_python.py
"""

from __future__ import annotations

import asyncio
import os

import boto3

from agentic_primitives_gateway_client import AgenticPlatformClient, Memory

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8000")
MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"


async def main():
    # Connect to the gateway
    client = AgenticPlatformClient(GATEWAY_URL, aws_from_environment=True)
    memory = Memory(client, namespace="agent:quickstart")

    # Set up Bedrock for LLM inference
    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

    print("Plain Python agent with gateway memory. Type 'quit' to exit.\n")

    messages = []
    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() in ("quit", "exit"):
            break

        # Search memory for relevant context
        context = await memory.recall_context(user_input)
        if context:
            print(f"  [memory] Found {len(context)} relevant memories")

        # Build the prompt with memory context
        system = "You are a helpful assistant with long-term memory."
        if context:
            system += f"\n\nRelevant memories:\n{context}"

        messages.append({"role": "user", "content": [{"text": user_input}]})

        # Call Bedrock
        response = bedrock.converse(
            modelId=MODEL_ID,
            messages=messages,
            system=[{"text": system}],
        )

        reply = "".join(b["text"] for b in response["output"]["message"]["content"] if "text" in b)
        messages.append({"role": "assistant", "content": [{"text": reply}]})
        print(f"\nAssistant: {reply}\n")

        # Store the turn in gateway memory
        await memory.store_turn(user_input, reply)


if __name__ == "__main__":
    asyncio.run(main())
