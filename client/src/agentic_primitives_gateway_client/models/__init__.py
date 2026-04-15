__all__ = ["LLMGateway"]


def __getattr__(name: str):
    if name == "LLMGateway":
        from agentic_primitives_gateway_client.models.llmgateway.strands import LLMGateway

        return LLMGateway
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
