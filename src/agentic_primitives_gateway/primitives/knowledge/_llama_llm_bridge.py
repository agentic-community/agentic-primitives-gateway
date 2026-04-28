"""Adapter that lets LlamaIndex synthesis calls flow through ``registry.llm``.

LlamaIndex's ``QueryEngine`` wants a ``LLM`` instance.  Normally that's
``BedrockLLM``, ``OpenAI``, etc. — each with its own credentials, its own
logging, its own token accounting.  Wiring one of those *inside* the
knowledge backend would duplicate everything the gateway's ``llm``
primitive already does (provider routing via ``X-Provider-*``,
per-request OIDC-resolved credentials, LLM audit events, token
counters), and the synthesis step would bypass the compliance trail.

``GatewayLlamaLLM`` is a thin ``CustomLLM`` whose ``complete`` methods
call ``registry.llm.route_request(...)``.  The registry resolves the
active LLM backend at call time, so contextvars evaluate per-request.
Every knowledge ``query()`` then emits the same LLM audit event + metric
set as any other gateway LLM call, and respects whatever provider the
caller pinned with ``X-Provider-Llm``.

The bridge is imported lazily from ``llamaindex.py`` so the LlamaIndex
dependency is optional — installing just the core gateway doesn't
require ``llama-index-core``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from typing import Any

# Import guard: if llama-index-core isn't installed, the whole module is
# importable but instantiating ``GatewayLlamaLLM`` raises with a clear
# message — same pattern used by LlamaIndex's optional integrations.
try:  # pragma: no cover - exercised by the backend tests that install llama-index
    from llama_index.core.base.llms.types import (
        ChatMessage,
        ChatResponse,
        ChatResponseAsyncGen,
        ChatResponseGen,
        CompletionResponse,
        CompletionResponseAsyncGen,
        CompletionResponseGen,
        LLMMetadata,
    )
    from llama_index.core.llms.custom import CustomLLM

    _LLAMA_INDEX_AVAILABLE = True
except Exception:  # pragma: no cover
    ChatMessage = Any  # type: ignore[assignment,misc]
    ChatResponse = Any  # type: ignore[assignment,misc]
    ChatResponseAsyncGen = Any  # type: ignore[assignment,misc]
    ChatResponseGen = Any  # type: ignore[assignment,misc]
    CompletionResponse = Any  # type: ignore[assignment,misc]
    CompletionResponseAsyncGen = Any  # type: ignore[assignment,misc]
    CompletionResponseGen = Any  # type: ignore[assignment,misc]
    LLMMetadata = Any  # type: ignore[assignment,misc]
    CustomLLM = object  # type: ignore[assignment,misc]
    _LLAMA_INDEX_AVAILABLE = False


_DEFAULT_MAX_TOKENS = 2048
_DEFAULT_CONTEXT_WINDOW = 200_000


def _require_llama_index() -> None:
    if not _LLAMA_INDEX_AVAILABLE:
        raise ImportError(
            "LlamaIndex is not installed.  Install with "
            "`pip install 'agentic-primitives-gateway[knowledge-llamaindex]'` to "
            "use LlamaIndexKnowledgeProvider with native query synthesis."
        )


def _build_model_request(
    prompt: str,
    *,
    model: str | None,
    backend_name: str | None,
    max_tokens: int,
    temperature: float | None,
) -> dict[str, Any]:
    """Shape a prompt into the gateway LLM ``route_request`` dict."""
    request: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if model:
        request["model"] = model
    if temperature is not None:
        request["temperature"] = temperature
    if backend_name:
        # Mirrors X-Provider-Llm pass-through: lets a knowledge backend
        # pin a specific gateway LLM backend for synthesis.
        request["_provider_override"] = backend_name
    return request


class GatewayLlamaLLM(CustomLLM):  # type: ignore[misc,valid-type]
    """LlamaIndex ``CustomLLM`` that delegates to ``registry.llm``.

    All completion calls route through the gateway's LLM primitive, so
    credentials, provider routing, audit events, and token accounting
    are inherited for free.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        backend_name: str | None = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float | None = None,
        context_window: int = _DEFAULT_CONTEXT_WINDOW,
    ) -> None:
        _require_llama_index()
        super().__init__()
        self._model = model
        self._backend_name = backend_name
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._context_window = context_window

    @property
    def metadata(self) -> LLMMetadata:  # type: ignore[override]
        return LLMMetadata(
            context_window=self._context_window,
            num_output=self._max_tokens,
            model_name=self._model or "gateway-llm",
            is_chat_model=True,
        )

    # ── Sync interface (LlamaIndex calls these from the sync query engine).
    # We bridge back to the gateway's async LLM via asyncio.run on the
    # current event loop.  In practice LlamaIndex is invoked from inside
    # ``SyncRunnerMixin._run_sync``, so we're already off the main loop
    # and can create a short-lived loop here without conflict.

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:  # type: ignore[override]
        response = asyncio.run(self._call_llm(prompt))
        return CompletionResponse(text=self._extract_text(response))

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:  # type: ignore[override]
        # Stream via the gateway's streaming route_request_stream.  We
        # collect into a list off-loop then yield sync for LlamaIndex's
        # sync generator protocol.
        chunks = asyncio.run(self._stream_llm(prompt))

        def _gen() -> Generator[CompletionResponse]:
            accumulated = ""
            for piece in chunks:
                accumulated += piece
                yield CompletionResponse(text=accumulated, delta=piece)

        return _gen()

    # ── Async interface (preferred when LlamaIndex's async APIs are used).

    async def acomplete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:  # type: ignore[override]
        response = await self._call_llm(prompt)
        return CompletionResponse(text=self._extract_text(response))

    async def astream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseAsyncGen:  # type: ignore[override]
        async def _gen() -> AsyncGenerator[CompletionResponse]:
            accumulated = ""
            async for piece in self._stream_llm_async(prompt):
                accumulated += piece
                yield CompletionResponse(text=accumulated, delta=piece)

        return _gen()

    # Chat versions delegate to completion (we normalize messages → prompt).
    def chat(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponse:  # type: ignore[override]
        prompt = self._messages_to_prompt(messages)
        completion = self.complete(prompt)
        return self._completion_to_chat(completion)

    async def achat(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponse:  # type: ignore[override]
        prompt = self._messages_to_prompt(messages)
        completion = await self.acomplete(prompt)
        return self._completion_to_chat(completion)

    def stream_chat(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponseGen:  # type: ignore[override]
        prompt = self._messages_to_prompt(messages)
        for completion in self.stream_complete(prompt):
            yield self._completion_to_chat(completion)

    async def astream_chat(self, messages: list[ChatMessage], **kwargs: Any) -> ChatResponseAsyncGen:  # type: ignore[override]
        prompt = self._messages_to_prompt(messages)

        async def _gen() -> AsyncGenerator[ChatResponse]:
            async for completion in await self.astream_complete(prompt):
                yield self._completion_to_chat(completion)

        return _gen()

    # ── Internals ──────────────────────────────────────────────────────

    async def _call_llm(self, prompt: str) -> dict[str, Any]:
        # Deferred import — the registry pulls the whole provider stack,
        # so we don't want it evaluated at module import.
        from agentic_primitives_gateway.registry import registry

        request = _build_model_request(
            prompt,
            model=self._model,
            backend_name=self._backend_name,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        return await registry.llm.route_request(request)

    async def _stream_llm(self, prompt: str) -> list[str]:
        chunks: list[str] = []
        async for piece in self._stream_llm_async(prompt):
            chunks.append(piece)
        return chunks

    async def _stream_llm_async(self, prompt: str) -> AsyncGenerator[str]:
        from agentic_primitives_gateway.registry import registry

        request = _build_model_request(
            prompt,
            model=self._model,
            backend_name=self._backend_name,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        async for event in registry.llm.route_request_stream(request):
            if isinstance(event, dict) and event.get("type") == "content_delta":
                delta = event.get("delta", "")
                if delta:
                    yield delta

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        content = response.get("content") or ""
        if isinstance(content, list):
            # Some providers return content as a list of blocks; concatenate the text ones.
            return "".join(block.get("text", "") for block in content if isinstance(block, dict))
        return str(content)

    @staticmethod
    def _messages_to_prompt(messages: list[ChatMessage]) -> str:
        parts: list[str] = []
        for msg in messages:
            role = getattr(msg, "role", "user")
            content = getattr(msg, "content", "") or ""
            parts.append(f"{role}: {content}")
        return "\n".join(parts)

    @staticmethod
    def _completion_to_chat(completion: CompletionResponse) -> ChatResponse:
        # Lazy import to avoid referencing the symbol at module init when
        # llama-index isn't installed.
        from llama_index.core.base.llms.types import ChatMessage as _ChatMessage
        from llama_index.core.base.llms.types import ChatResponse as _ChatResponse
        from llama_index.core.base.llms.types import MessageRole

        message = _ChatMessage(role=MessageRole.ASSISTANT, content=completion.text or "")
        return _ChatResponse(message=message, delta=completion.delta)
