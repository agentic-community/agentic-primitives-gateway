__all__ = ["LLMGateway"]


def __getattr__(name: str):
    if name == "LLMGateway":
        # Ambiguous without framework context — prefer explicit imports:
        #   from agentic_primitives_gateway_client.models.llmgateway.strands import LLMGateway
        #   from agentic_primitives_gateway_client.models.llmgateway.langchain import LLMGateway
        # Default to strands.
        from agentic_primitives_gateway_client.models.llmgateway.strands import LLMGateway

        return LLMGateway
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
