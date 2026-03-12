from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentic_primitives_gateway.auth.api_key import ApiKeyAuthBackend


def _make_request(headers: dict[str, str]) -> MagicMock:
    request = MagicMock()
    request.headers = headers
    return request


class TestApiKeyAuthBackend:
    def _make_backend(self) -> ApiKeyAuthBackend:
        return ApiKeyAuthBackend(
            api_keys=[
                {
                    "key": "sk-test-123",
                    "principal_id": "alice",
                    "principal_type": "user",
                    "groups": ["engineering", "admin"],
                    "scopes": ["admin"],
                },
                {
                    "key": "sk-service-456",
                    "principal_id": "ci-bot",
                    "principal_type": "service",
                    "groups": [],
                    "scopes": ["agents:read"],
                },
                {
                    "key": "sk-minimal",
                },
            ]
        )

    @pytest.mark.asyncio
    async def test_bearer_token_auth(self):
        backend = self._make_backend()
        request = _make_request({"authorization": "Bearer sk-test-123"})
        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.id == "alice"
        assert principal.type == "user"
        assert principal.groups == frozenset({"engineering", "admin"})
        assert principal.scopes == frozenset({"admin"})

    @pytest.mark.asyncio
    async def test_x_api_key_header(self):
        backend = self._make_backend()
        request = _make_request({"x-api-key": "sk-service-456"})
        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.id == "ci-bot"
        assert principal.type == "service"
        assert principal.groups == frozenset()
        assert principal.scopes == frozenset({"agents:read"})

    @pytest.mark.asyncio
    async def test_bearer_takes_precedence_over_x_api_key(self):
        backend = self._make_backend()
        request = _make_request(
            {
                "authorization": "Bearer sk-test-123",
                "x-api-key": "sk-service-456",
            }
        )
        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.id == "alice"

    @pytest.mark.asyncio
    async def test_invalid_key_returns_none(self):
        backend = self._make_backend()
        request = _make_request({"authorization": "Bearer bad-key"})
        principal = await backend.authenticate(request)
        assert principal is None

    @pytest.mark.asyncio
    async def test_no_credentials_returns_none(self):
        backend = self._make_backend()
        request = _make_request({})
        principal = await backend.authenticate(request)
        assert principal is None

    @pytest.mark.asyncio
    async def test_non_bearer_auth_scheme_ignored(self):
        backend = self._make_backend()
        request = _make_request({"authorization": "Basic dXNlcjpwYXNz"})
        principal = await backend.authenticate(request)
        assert principal is None

    @pytest.mark.asyncio
    async def test_minimal_key_config_uses_defaults(self):
        backend = self._make_backend()
        request = _make_request({"x-api-key": "sk-minimal"})
        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.id == "sk-minimal"  # key used as principal_id
        assert principal.type == "user"  # default type
        assert principal.groups == frozenset()
        assert principal.scopes == frozenset()

    @pytest.mark.asyncio
    async def test_empty_keys_list(self):
        backend = ApiKeyAuthBackend(api_keys=[])
        request = _make_request({"authorization": "Bearer anything"})
        principal = await backend.authenticate(request)
        assert principal is None

    @pytest.mark.asyncio
    async def test_bearer_whitespace_stripped(self):
        backend = self._make_backend()
        request = _make_request({"authorization": "Bearer  sk-test-123 "})
        principal = await backend.authenticate(request)
        assert principal is not None
        assert principal.id == "alice"
