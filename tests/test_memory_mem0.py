from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from agentic_primitives_gateway.context import AWSCredentials
from agentic_primitives_gateway.primitives.memory.mem0_provider import (
    Mem0MemoryProvider,
    _with_aws_env,
)


class TestWithAwsEnv:
    """Tests for the _with_aws_env context manager."""

    def test_injects_and_restores_env_vars(self):
        creds = AWSCredentials(
            access_key_id="AKID",
            secret_access_key="SECRET",
            session_token="TOKEN",
            region="us-west-2",
        )
        original_key = os.environ.get("AWS_ACCESS_KEY_ID")

        with _with_aws_env(creds):
            assert os.environ["AWS_ACCESS_KEY_ID"] == "AKID"
            assert os.environ["AWS_SECRET_ACCESS_KEY"] == "SECRET"
            assert os.environ["AWS_SESSION_TOKEN"] == "TOKEN"
            assert os.environ["AWS_REGION"] == "us-west-2"

        # Verify restoration
        if original_key is None:
            assert "AWS_ACCESS_KEY_ID" not in os.environ or os.environ.get("AWS_ACCESS_KEY_ID") != "AKID"
        else:
            assert os.environ.get("AWS_ACCESS_KEY_ID") == original_key

    def test_none_creds_server_allowed(self):
        with (
            patch("agentic_primitives_gateway.context._server_credentials_allowed", return_value=True),
            _with_aws_env(None),
        ):
            pass  # Should not raise

    def test_none_creds_server_disallowed(self):
        with (
            patch("agentic_primitives_gateway.context._server_credentials_allowed", return_value=False),
            pytest.raises(ValueError, match="No AWS credentials"),
            _with_aws_env(None),
        ):
            pass

    def test_partial_creds_no_token(self):
        creds = AWSCredentials(
            access_key_id="AKID",
            secret_access_key="SECRET",
        )
        with _with_aws_env(creds):
            assert os.environ.get("AWS_ACCESS_KEY_ID") == "AKID"
            # session_token is None, should not be set
            # (only truthy values are set)


@patch("agentic_primitives_gateway.primitives.memory.mem0_provider.get_aws_credentials")
class TestMem0MemoryProvider:
    """Tests for the Mem0 memory provider."""

    def _make_provider(self, **kwargs):
        prov = Mem0MemoryProvider(**kwargs)
        # Pre-initialize the client mock to avoid actual mem0 initialization
        prov._client = MagicMock()
        return prov

    @pytest.mark.asyncio
    async def test_store_new_record(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": []}

        result = await provider.store(namespace="ns", key="k1", content="hello")

        assert result.namespace == "ns"
        assert result.key == "k1"
        assert result.content == "hello"
        provider._client.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_update_existing(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": [{"id": "mem-1", "metadata": {"_agentic_key": "k1"}}]}

        await provider.store(namespace="ns", key="k1", content="updated")

        provider._client.update.assert_called_once_with("mem-1", data="updated")

    @pytest.mark.asyncio
    async def test_retrieve_found(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {
            "results": [
                {
                    "id": "mem-1",
                    "memory": "found data",
                    "metadata": {"_agentic_key": "k1"},
                    "created_at": None,
                    "updated_at": None,
                },
            ]
        }

        result = await provider.retrieve(namespace="ns", key="k1")

        assert result is not None
        assert result.content == "found data"

    @pytest.mark.asyncio
    async def test_retrieve_not_found(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": []}

        result = await provider.retrieve(namespace="ns", key="missing")
        assert result is None

    @pytest.mark.asyncio
    async def test_search(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.search.return_value = {
            "results": [
                {
                    "id": "mem-1",
                    "memory": "result",
                    "metadata": {"_agentic_key": "k1"},
                    "score": 0.9,
                    "created_at": None,
                    "updated_at": None,
                },
            ]
        }

        results = await provider.search(namespace="ns", query="test", top_k=5)

        assert len(results) == 1
        assert results[0].score == 0.9

    @pytest.mark.asyncio
    async def test_search_list_format(self, mock_get_creds):
        """Test when search returns a list instead of dict."""
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.search.return_value = [
            {"id": "mem-1", "memory": "result", "metadata": {}, "score": 0.8, "created_at": None, "updated_at": None},
        ]

        results = await provider.search(namespace="ns", query="test")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_delete_success(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": [{"id": "mem-1", "metadata": {"_agentic_key": "k1"}}]}

        result = await provider.delete(namespace="ns", key="k1")

        assert result is True
        provider._client.delete.assert_called_once_with("mem-1")

    @pytest.mark.asyncio
    async def test_delete_not_found(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": []}

        result = await provider.delete(namespace="ns", key="missing")
        assert result is False

    @pytest.mark.asyncio
    async def test_list_memories(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {
            "results": [
                {
                    "id": "1",
                    "memory": "mem-a",
                    "metadata": {"_agentic_key": "ka"},
                    "created_at": None,
                    "updated_at": None,
                },
                {
                    "id": "2",
                    "memory": "mem-b",
                    "metadata": {"_agentic_key": "kb", "cat": "X"},
                    "created_at": None,
                    "updated_at": None,
                },
            ]
        }

        records = await provider.list_memories(namespace="ns")
        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_list_memories_with_filter(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {
            "results": [
                {
                    "id": "1",
                    "memory": "a",
                    "metadata": {"_agentic_key": "ka", "cat": "A"},
                    "created_at": None,
                    "updated_at": None,
                },
                {
                    "id": "2",
                    "memory": "b",
                    "metadata": {"_agentic_key": "kb", "cat": "B"},
                    "created_at": None,
                    "updated_at": None,
                },
            ]
        }

        records = await provider.list_memories(namespace="ns", filters={"cat": "A"})
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_list_memories_with_offset_limit(self, mock_get_creds):
        creds = AWSCredentials(access_key_id="AK", secret_access_key="SK", region="us-east-1")
        mock_get_creds.return_value = creds
        provider = self._make_provider()
        provider._client.get_all.return_value = {
            "results": [
                {"id": str(i), "memory": f"m{i}", "metadata": {}, "created_at": None, "updated_at": None}
                for i in range(5)
            ]
        }

        records = await provider.list_memories(namespace="ns", limit=2, offset=1)
        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_healthcheck_success(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider()
        provider._client.get_all.return_value = {"results": []}

        with patch("agentic_primitives_gateway.context._server_credentials_allowed", return_value=True):
            assert await provider.healthcheck() is True

    @pytest.mark.asyncio
    async def test_healthcheck_failure(self, mock_get_creds):
        mock_get_creds.return_value = None
        provider = self._make_provider()
        provider._client.get_all.side_effect = Exception("connection failed")

        with patch("agentic_primitives_gateway.context._server_credentials_allowed", return_value=True):
            assert await provider.healthcheck() is False

    @pytest.mark.asyncio
    async def test_to_record_with_timestamps(self, mock_get_creds):
        entry = {
            "memory": "test",
            "metadata": {"_agentic_key": "k"},
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-01-02T00:00:00",
        }
        record = Mem0MemoryProvider._to_record("ns", "k", entry)
        assert record.created_at.year == 2025
        assert record.updated_at.day == 2
