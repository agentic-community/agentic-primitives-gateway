"""Python client for the Agentic Primitives Gateway API.

Usage::

    from agentic_primitives_gateway_client import AgenticPlatformClient

    async with AgenticPlatformClient("http://localhost:8000") as client:
        record = await client.store_memory("agent:my-agent", "key1", "some content")
        results = await client.search_memory("agent:my-agent", "some query")

With AWS credential pass-through for AgentCore backends::

    # Explicit credentials
    client = AgenticPlatformClient(
        "http://localhost:8000",
        aws_access_key_id="AKIA...",
        aws_secret_access_key="...",
        aws_session_token="...",     # optional, for temporary creds
        aws_region="us-east-1",      # optional
    )

    # Auto-resolve from environment (EKS Pod Identity, IRSA, env vars, etc.)
    # Requires: pip install agentic-primitives-gateway-client[aws]
    client = AgenticPlatformClient(
        "http://localhost:8000",
        aws_from_environment=True,   # resolves fresh creds on every request
    )
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from agentic_primitives_gateway_client._build_info import BUILD_REF

logger = logging.getLogger(__name__)


class AgenticPlatformError(Exception):
    """Raised when the platform API returns an error response."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class AgenticPlatformClient:
    """Async client for the Agentic Primitives Gateway service.

    Covers all platform primitives: memory, observability, gateway, tools,
    identity, code interpreter, and browser.

    AWS credentials can be provided at construction time or updated later
    via :meth:`set_aws_credentials`. When set, they are sent as headers on
    every request so the server can forward them to AgentCore backends.

    For EKS Pod Identity, IRSA, or any environment where boto3 can resolve
    credentials automatically, use ``aws_from_environment=True``. This
    resolves fresh credentials on every request, so temporary tokens are
    always up-to-date even after automatic refresh. Requires ``boto3``
    (install with ``pip install agentic-primitives-gateway-client[aws]``).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout: float = 30.0,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_session_token: str | None = None,
        aws_region: str | None = None,
        aws_from_environment: bool = False,
        aws_profile: str | None = None,
        provider: str | None = None,
        max_retries: int = 3,
        retry_backoff: float = 0.5,
        retry_status_codes: set[int] | None = None,
        **httpx_kwargs: Any,
    ) -> None:
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        self._retry_status_codes = retry_status_codes if retry_status_codes is not None else {502, 503, 504}

        self._aws_headers: dict[str, str] = {}
        self._aws_from_environment = aws_from_environment
        self._aws_profile = aws_profile
        self._aws_env_region = aws_region
        self._provider_headers: dict[str, str] = {}
        self._service_cred_headers: dict[str, str] = {}

        if provider:
            self.set_provider(provider)

        if aws_from_environment:
            # Validate boto3 is available at init time
            try:
                import boto3  # noqa: F401
            except ImportError:
                raise ImportError(
                    "boto3 is required for aws_from_environment=True. "
                    "Install it with: pip install agentic-primitives-gateway-client[aws]"
                ) from None
        elif aws_access_key_id and aws_secret_access_key:
            self.set_aws_credentials(
                access_key_id=aws_access_key_id,
                secret_access_key=aws_secret_access_key,
                session_token=aws_session_token,
                region=aws_region,
            )

        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            **httpx_kwargs,
        )

        logger.info("agentic-primitives-gateway-client build=%s", BUILD_REF)

    def set_aws_credentials(
        self,
        access_key_id: str,
        secret_access_key: str,
        session_token: str | None = None,
        region: str | None = None,
    ) -> None:
        """Set AWS credentials to be sent with every request.

        These are forwarded by the server to AgentCore provider backends.
        Call with temporary STS credentials for scoped access.
        """
        self._aws_headers = {
            "x-aws-access-key-id": access_key_id,
            "x-aws-secret-access-key": secret_access_key,
        }
        if session_token:
            self._aws_headers["x-aws-session-token"] = session_token
        if region:
            self._aws_headers["x-aws-region"] = region

    def clear_aws_credentials(self) -> None:
        """Remove AWS credentials from future requests."""
        self._aws_headers = {}

    def set_provider(self, name: str) -> None:
        """Set the default provider for all primitives.

        The server routes requests to the named backend. Use
        :meth:`set_provider_for` to override individual primitives.
        """
        self._provider_headers["x-provider"] = name

    def set_provider_for(self, primitive: str, name: str) -> None:
        """Set the provider for a specific primitive.

        Args:
            primitive: One of 'memory', 'identity', 'code_interpreter',
                'browser', 'observability', 'gateway', 'tools'.
            name: The backend name as configured on the server.
        """
        header = f"x-provider-{primitive.replace('_', '-')}"
        self._provider_headers[header] = name

    def clear_provider(self) -> None:
        """Remove all provider routing overrides."""
        self._provider_headers = {}

    def set_service_credentials(self, service: str, credentials: dict[str, str]) -> None:
        """Set credentials for a service to be sent with every request.

        The server forwards these to the appropriate provider backend.

        Args:
            service: Service name (e.g., 'langfuse', 'openai').
            credentials: Key-value pairs for this service.

        Example::

            client.set_service_credentials("langfuse", {
                "public_key": "pk-...",
                "secret_key": "sk-...",
                "base_url": "https://cloud.langfuse.com",
            })
        """
        # Remove any existing headers for this service
        self._service_cred_headers = {
            k: v for k, v in self._service_cred_headers.items() if not k.startswith(f"x-cred-{service}-")
        }
        # Add new headers
        for key, value in credentials.items():
            header = f"x-cred-{service}-{key.replace('_', '-')}"
            self._service_cred_headers[header] = value

    def clear_service_credentials(self, service: str | None = None) -> None:
        """Remove service credentials.

        Args:
            service: Service name to clear, or None to clear all.
        """
        if service is None:
            self._service_cred_headers = {}
        else:
            self._service_cred_headers = {
                k: v for k, v in self._service_cred_headers.items() if not k.startswith(f"x-cred-{service}-")
            }

    async def __aenter__(self) -> AgenticPlatformClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            detail = resp.text
            try:  # noqa: SIM105
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise AgenticPlatformError(resp.status_code, detail)

    @staticmethod
    def _json_dict(resp: httpx.Response) -> dict[str, Any]:
        """Type-safe wrapper for resp.json() returning a dict."""
        data: dict[str, Any] = resp.json()
        return data

    @property
    def _headers(self) -> dict[str, str]:
        try:
            headers: dict[str, str] = {}
            if self._aws_from_environment:
                headers.update(self._resolve_aws_headers())
            else:
                headers.update(self._aws_headers)
            headers.update(self._provider_headers)
            headers.update(self._service_cred_headers)
            return headers
        except AttributeError:
            return {}

    def _resolve_aws_headers(self) -> dict[str, str]:
        """Resolve fresh AWS credentials from boto3's credential chain.

        Called on every request when aws_from_environment=True. This handles
        automatic token refresh for EKS Pod Identity, IRSA, instance profiles,
        and any other credential source boto3 supports.
        """
        import boto3

        session = boto3.Session(profile_name=self._aws_profile)
        creds = session.get_credentials()
        if creds is None:
            return {}

        resolved = creds.get_frozen_credentials()
        headers: dict[str, str] = {
            "x-aws-access-key-id": resolved.access_key,
            "x-aws-secret-access-key": resolved.secret_key,
        }
        if resolved.token:
            headers["x-aws-session-token"] = resolved.token

        region = self._aws_env_region or session.region_name
        if region:
            headers["x-aws-region"] = region

        return headers

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_exc: httpx.TransportError | None = None
        resp: httpx.Response | None = None

        for attempt in range(1 + self._max_retries):
            try:
                resp = await self._client.request(method, path, headers=self._headers, **kwargs)
                last_exc = None
            except httpx.TransportError as exc:
                last_exc = exc
                resp = None

            # Decide whether to retry
            should_retry = attempt < self._max_retries and (
                last_exc is not None or (resp is not None and resp.status_code in self._retry_status_codes)
            )
            if not should_retry:
                break

            delay = self._retry_backoff * (2**attempt) + random.random() * self._retry_backoff
            await asyncio.sleep(delay)

        if last_exc is not None:
            raise last_exc

        assert resp is not None
        return resp

    async def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._request("GET", path, **kwargs)

    async def _post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._request("POST", path, **kwargs)

    async def _delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self._request("DELETE", path, **kwargs)

    # ── Health ──────────────────────────────────────────────────────────

    async def healthz(self) -> dict[str, Any]:
        resp = await self._get("/healthz")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def readyz(self) -> dict[str, Any]:
        resp = await self._get("/readyz")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Providers ───────────────────────────────────────────────────────

    async def list_providers(self) -> dict[str, Any]:
        """Discover available providers for each primitive on the server."""
        resp = await self._get("/api/v1/providers")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Memory ──────────────────────────────────────────────────────────

    async def store_memory(
        self,
        namespace: str,
        key: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/memory/{namespace}",
            json={"key": key, "content": content, "metadata": metadata or {}},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def retrieve_memory(self, namespace: str, key: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/memory/{namespace}/{key}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_memories(
        self,
        namespace: str,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        resp = await self._get(
            f"/api/v1/memory/{namespace}",
            params={"limit": limit, "offset": offset},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def search_memory(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/memory/{namespace}/search",
            json={"query": query, "top_k": top_k, "filters": filters or {}},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def delete_memory(self, namespace: str, key: str) -> None:
        resp = await self._delete(f"/api/v1/memory/{namespace}/{key}")
        if resp.status_code == 204:
            return
        self._raise_for_status(resp)

    # ── Memory: Conversation events ────────────────────────────────────

    async def create_event(
        self,
        actor_id: str,
        session_id: str,
        messages: list[dict[str, str]],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/memory/sessions/{actor_id}/{session_id}/events",
            json={"messages": messages, "metadata": metadata or {}},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_events(
        self,
        actor_id: str,
        session_id: str,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        resp = await self._get(
            f"/api/v1/memory/sessions/{actor_id}/{session_id}/events",
            params={"limit": limit},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def get_event(
        self,
        actor_id: str,
        session_id: str,
        event_id: str,
    ) -> dict[str, Any]:
        resp = await self._get(
            f"/api/v1/memory/sessions/{actor_id}/{session_id}/events/{event_id}",
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def delete_event(
        self,
        actor_id: str,
        session_id: str,
        event_id: str,
    ) -> None:
        resp = await self._delete(
            f"/api/v1/memory/sessions/{actor_id}/{session_id}/events/{event_id}",
        )
        if resp.status_code == 204:
            return
        self._raise_for_status(resp)

    async def get_last_turns(
        self,
        actor_id: str,
        session_id: str,
        *,
        k: int = 5,
    ) -> dict[str, Any]:
        resp = await self._get(
            f"/api/v1/memory/sessions/{actor_id}/{session_id}/turns",
            params={"k": k},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Memory: Session management ─────────────────────────────────────

    async def list_actors(self) -> dict[str, Any]:
        resp = await self._get("/api/v1/memory/actors")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_memory_sessions(self, actor_id: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/memory/actors/{actor_id}/sessions")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Memory: Branch management ──────────────────────────────────────

    async def fork_conversation(
        self,
        actor_id: str,
        session_id: str,
        root_event_id: str,
        branch_name: str,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/memory/sessions/{actor_id}/{session_id}/branches",
            json={
                "root_event_id": root_event_id,
                "branch_name": branch_name,
                "messages": messages,
            },
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_branches(
        self,
        actor_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        resp = await self._get(
            f"/api/v1/memory/sessions/{actor_id}/{session_id}/branches",
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Memory: Control plane ──────────────────────────────────────────

    async def create_memory_resource(
        self,
        name: str,
        *,
        strategies: list[dict[str, Any]] | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        resp = await self._post(
            "/api/v1/memory/resources",
            json={"name": name, "strategies": strategies or [], "description": description},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def get_memory_resource(self, memory_id: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/memory/resources/{memory_id}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_memory_resources(self) -> dict[str, Any]:
        resp = await self._get("/api/v1/memory/resources")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def delete_memory_resource(self, memory_id: str) -> None:
        resp = await self._delete(f"/api/v1/memory/resources/{memory_id}")
        if resp.status_code == 204:
            return
        self._raise_for_status(resp)

    # ── Memory: Strategy management ────────────────────────────────────

    async def list_strategies(self, memory_id: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/memory/resources/{memory_id}/strategies")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def add_strategy(
        self,
        memory_id: str,
        strategy: dict[str, Any],
    ) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/memory/resources/{memory_id}/strategies",
            json={"strategy": strategy},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def delete_strategy(self, memory_id: str, strategy_id: str) -> None:
        resp = await self._delete(
            f"/api/v1/memory/resources/{memory_id}/strategies/{strategy_id}",
        )
        if resp.status_code == 204:
            return
        self._raise_for_status(resp)

    # ── Identity ────────────────────────────────────────────────────────

    async def get_token(
        self,
        credential_provider: str,
        workload_token: str,
        *,
        auth_flow: str = "M2M",
        scopes: list[str] | None = None,
        callback_url: str | None = None,
        force_auth: bool = False,
        session_uri: str | None = None,
        custom_state: str | None = None,
        custom_parameters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "credential_provider": credential_provider,
            "workload_token": workload_token,
            "auth_flow": auth_flow,
            "scopes": scopes or [],
        }
        if callback_url is not None:
            body["callback_url"] = callback_url
        if force_auth:
            body["force_auth"] = force_auth
        if session_uri is not None:
            body["session_uri"] = session_uri
        if custom_state is not None:
            body["custom_state"] = custom_state
        if custom_parameters is not None:
            body["custom_parameters"] = custom_parameters
        resp = await self._post("/api/v1/identity/token", json=body)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def get_api_key(
        self,
        credential_provider: str,
        workload_token: str,
    ) -> dict[str, Any]:
        resp = await self._post(
            "/api/v1/identity/api-key",
            json={"credential_provider": credential_provider, "workload_token": workload_token},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def get_workload_token(
        self,
        workload_name: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"workload_name": workload_name}
        if user_token is not None:
            body["user_token"] = user_token
        if user_id is not None:
            body["user_id"] = user_id
        resp = await self._post("/api/v1/identity/workload-token", json=body)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def complete_auth(
        self,
        session_uri: str,
        *,
        user_token: str | None = None,
        user_id: str | None = None,
    ) -> None:
        body: dict[str, Any] = {"session_uri": session_uri}
        if user_token is not None:
            body["user_token"] = user_token
        if user_id is not None:
            body["user_id"] = user_id
        resp = await self._post("/api/v1/identity/auth/complete", json=body)
        if resp.status_code == 204:
            return
        self._raise_for_status(resp)

    async def list_credential_providers(self) -> dict[str, Any]:
        resp = await self._get("/api/v1/identity/credential-providers")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def create_credential_provider(
        self,
        name: str,
        provider_type: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await self._post(
            "/api/v1/identity/credential-providers",
            json={"name": name, "provider_type": provider_type, "config": config or {}},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def get_credential_provider(self, name: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/identity/credential-providers/{name}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def update_credential_provider(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        resp = await self._request("PUT", f"/api/v1/identity/credential-providers/{name}", json={"config": config})
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def delete_credential_provider(self, name: str) -> None:
        resp = await self._delete(f"/api/v1/identity/credential-providers/{name}")
        if resp.status_code == 204:
            return
        self._raise_for_status(resp)

    async def create_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name}
        if allowed_return_urls is not None:
            body["allowed_return_urls"] = allowed_return_urls
        resp = await self._post("/api/v1/identity/workload-identities", json=body)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def get_workload_identity(self, name: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/identity/workload-identities/{name}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def update_workload_identity(
        self,
        name: str,
        *,
        allowed_return_urls: list[str] | None = None,
    ) -> dict[str, Any]:
        resp = await self._request(
            "PUT",
            f"/api/v1/identity/workload-identities/{name}",
            json={"allowed_return_urls": allowed_return_urls or []},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def delete_workload_identity(self, name: str) -> None:
        resp = await self._delete(f"/api/v1/identity/workload-identities/{name}")
        if resp.status_code == 204:
            return
        self._raise_for_status(resp)

    async def list_workload_identities(self) -> dict[str, Any]:
        resp = await self._get("/api/v1/identity/workload-identities")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Code Interpreter ────────────────────────────────────────────────

    async def start_code_session(
        self,
        session_id: str | None = None,
        language: str = "python",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await self._post(
            "/api/v1/code-interpreter/sessions",
            json={
                "session_id": session_id,
                "language": language,
                "config": config or {},
            },
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def stop_code_session(self, session_id: str) -> None:
        resp = await self._delete(f"/api/v1/code-interpreter/sessions/{session_id}")
        if resp.status_code == 204:
            return
        self._raise_for_status(resp)

    async def execute_code(
        self,
        session_id: str,
        code: str,
        language: str = "python",
    ) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/code-interpreter/sessions/{session_id}/execute",
            json={"code": code, "language": language},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_code_sessions(self) -> dict[str, Any]:
        resp = await self._get("/api/v1/code-interpreter/sessions")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def upload_file(
        self,
        session_id: str,
        filename: str,
        content: bytes,
    ) -> dict[str, Any]:
        resp = await self._client.post(
            f"/api/v1/code-interpreter/sessions/{session_id}/files",
            headers=self._headers,
            files={"file": (filename, content)},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def download_file(self, session_id: str, filename: str) -> bytes:
        resp = await self._get(f"/api/v1/code-interpreter/sessions/{session_id}/files/{filename}")
        self._raise_for_status(resp)
        return resp.content

    # ── Code Interpreter: extended ─────────────────────────────────────

    async def get_code_session(self, session_id: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/code-interpreter/sessions/{session_id}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def get_execution_history(
        self,
        session_id: str,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        resp = await self._get(
            f"/api/v1/code-interpreter/sessions/{session_id}/history",
            params={"limit": limit},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Browser ─────────────────────────────────────────────────────────

    async def start_browser_session(
        self,
        session_id: str | None = None,
        viewport: dict[str, int] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resp = await self._post(
            "/api/v1/browser/sessions",
            json={
                "session_id": session_id,
                "viewport": viewport,
                "config": config or {},
            },
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def stop_browser_session(self, session_id: str) -> None:
        resp = await self._delete(f"/api/v1/browser/sessions/{session_id}")
        if resp.status_code == 204:
            return
        self._raise_for_status(resp)

    async def get_browser_session(self, session_id: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/browser/sessions/{session_id}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_browser_sessions(self) -> dict[str, Any]:
        resp = await self._get("/api/v1/browser/sessions")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def get_live_view_url(self, session_id: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/browser/sessions/{session_id}/live-view")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def browser_navigate(self, session_id: str, url: str) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/browser/sessions/{session_id}/navigate",
            json={"url": url},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def browser_screenshot(self, session_id: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/browser/sessions/{session_id}/screenshot")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def browser_get_content(self, session_id: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/browser/sessions/{session_id}/content")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def browser_click(self, session_id: str, selector: str) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/browser/sessions/{session_id}/click",
            json={"selector": selector},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def browser_type(self, session_id: str, selector: str, text: str) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/browser/sessions/{session_id}/type",
            json={"selector": selector, "text": text},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def browser_evaluate(self, session_id: str, expression: str) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/browser/sessions/{session_id}/evaluate",
            json={"expression": expression},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Observability ───────────────────────────────────────────────────

    async def ingest_trace(self, trace: dict[str, Any]) -> dict[str, Any]:
        resp = await self._post("/api/v1/observability/traces", json=trace)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def ingest_log(self, log_entry: dict[str, Any]) -> dict[str, Any]:
        resp = await self._post("/api/v1/observability/logs", json=log_entry)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def query_traces(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = await self._get("/api/v1/observability/traces", params=filters or {})
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Observability: extended ────────────────────────────────────────

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/observability/traces/{trace_id}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def update_trace(self, trace_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        resp = await self._request("PUT", f"/api/v1/observability/traces/{trace_id}", json=updates)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def log_generation(
        self,
        trace_id: str,
        generation: dict[str, Any],
    ) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/observability/traces/{trace_id}/generations",
            json=generation,
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def score_trace(
        self,
        trace_id: str,
        score: dict[str, Any],
    ) -> dict[str, Any]:
        resp = await self._post(
            f"/api/v1/observability/traces/{trace_id}/scores",
            json=score,
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_scores(self, trace_id: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/observability/traces/{trace_id}/scores")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_observability_sessions(
        self,
        user_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        if user_id:
            params["user_id"] = user_id
        resp = await self._get("/api/v1/observability/sessions", params=params)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def get_observability_session(self, session_id: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/observability/sessions/{session_id}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def flush_observability(self) -> dict[str, Any]:
        resp = await self._post("/api/v1/observability/flush")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Gateway ─────────────────────────────────────────────────────────

    async def completions(self, model_request: dict[str, Any]) -> dict[str, Any]:
        resp = await self._post("/api/v1/gateway/completions", json=model_request)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_models(self) -> dict[str, Any]:
        resp = await self._get("/api/v1/gateway/models")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Tools ───────────────────────────────────────────────────────────

    async def register_tool(self, tool_def: dict[str, Any]) -> dict[str, Any]:
        resp = await self._post("/api/v1/tools", json=tool_def)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_tools(self) -> dict[str, Any]:
        resp = await self._get("/api/v1/tools")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def invoke_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = await self._post(f"/api/v1/tools/{tool_name}/invoke", json={"params": params})
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def search_tools(self, query: str, max_results: int = 10) -> dict[str, Any]:
        resp = await self._get(
            "/api/v1/tools/search",
            params={"query": query, "max_results": max_results},
        )
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Tools: extended ────────────────────────────────────────────────

    async def get_tool(self, tool_name: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/tools/{tool_name}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def delete_tool(self, tool_name: str) -> None:
        resp = await self._delete(f"/api/v1/tools/{tool_name}")
        if resp.status_code == 204:
            return
        self._raise_for_status(resp)

    async def list_tool_servers(self) -> dict[str, Any]:
        resp = await self._get("/api/v1/tools/servers")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def get_tool_server(self, server_name: str) -> dict[str, Any]:
        resp = await self._get(f"/api/v1/tools/servers/{server_name}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def register_tool_server(self, server_config: dict[str, Any]) -> dict[str, Any]:
        resp = await self._post("/api/v1/tools/servers", json=server_config)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    # ── Agents ─────────────────────────────────────────────────────────

    async def create_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Create a new agent.

        Args:
            spec: Agent specification including at minimum ``name`` and ``model``.
        """
        resp = await self._post("/api/v1/agents", json=spec)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def list_agents(self) -> dict[str, Any]:
        """List all registered agents."""
        resp = await self._get("/api/v1/agents")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def get_agent(self, name: str) -> dict[str, Any]:
        """Get a single agent by name.

        Args:
            name: The agent name.
        """
        resp = await self._get(f"/api/v1/agents/{name}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def update_agent(self, name: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update an existing agent.

        Args:
            name: The agent name.
            updates: Fields to update (only non-None fields are applied).
        """
        resp = await self._request("PUT", f"/api/v1/agents/{name}", json=updates)
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def delete_agent(self, name: str) -> dict[str, Any]:
        """Delete an agent by name.

        Args:
            name: The agent name.
        """
        resp = await self._delete(f"/api/v1/agents/{name}")
        self._raise_for_status(resp)
        return self._json_dict(resp)

    async def chat_with_agent(
        self,
        name: str,
        message: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a chat message to a named agent.

        Args:
            name: The agent name.
            message: The user message.
            session_id: Optional session ID for conversation continuity.
        """
        body: dict[str, Any] = {"message": message}
        if session_id is not None:
            body["session_id"] = session_id
        resp = await self._post(f"/api/v1/agents/{name}/chat", json=body)
        self._raise_for_status(resp)
        return self._json_dict(resp)
