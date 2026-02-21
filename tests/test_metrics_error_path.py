from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentic_primitives_gateway.metrics import (
    ERROR_COUNT,
    REQUEST_COUNT,
    MetricsProxy,
)


class TestMetricsProxyErrorPath:
    """Test MetricsProxy error counter / re-raise logic."""

    @pytest.mark.asyncio
    async def test_error_increments_error_counter_and_reraises(self):
        mock_provider = MagicMock()
        mock_provider.some_method = AsyncMock(side_effect=RuntimeError("boom"))
        proxy = MetricsProxy(mock_provider, "test_prim", "test_provider")

        before_error = ERROR_COUNT.labels(
            primitive="test_prim",
            provider="test_provider",
            method="some_method",
            error_type="RuntimeError",
        )._value.get()

        before_request_error = REQUEST_COUNT.labels(
            primitive="test_prim",
            provider="test_provider",
            method="some_method",
            status="error",
        )._value.get()

        with pytest.raises(RuntimeError, match="boom"):
            await proxy.some_method()

        after_error = ERROR_COUNT.labels(
            primitive="test_prim",
            provider="test_provider",
            method="some_method",
            error_type="RuntimeError",
        )._value.get()

        after_request_error = REQUEST_COUNT.labels(
            primitive="test_prim",
            provider="test_provider",
            method="some_method",
            status="error",
        )._value.get()

        assert after_error == before_error + 1
        assert after_request_error == before_request_error + 1

    @pytest.mark.asyncio
    async def test_success_increments_success_counter(self):
        mock_provider = MagicMock()
        mock_provider.do_thing = AsyncMock(return_value="ok")
        proxy = MetricsProxy(mock_provider, "test_prim2", "test_provider2")

        before = REQUEST_COUNT.labels(
            primitive="test_prim2",
            provider="test_provider2",
            method="do_thing",
            status="success",
        )._value.get()

        result = await proxy.do_thing()
        assert result == "ok"

        after = REQUEST_COUNT.labels(
            primitive="test_prim2",
            provider="test_provider2",
            method="do_thing",
            status="success",
        )._value.get()
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_non_async_attribute_forwarded(self):
        mock_provider = MagicMock()
        mock_provider.some_property = "hello"
        proxy = MetricsProxy(mock_provider, "prim", "prov")
        assert proxy.some_property == "hello"

    @pytest.mark.asyncio
    async def test_private_method_not_wrapped(self):
        mock_provider = MagicMock()
        mock_provider._private_method = AsyncMock(return_value="secret")
        proxy = MetricsProxy(mock_provider, "prim", "prov")
        # Private methods are forwarded directly (not wrapped)
        result = await proxy._private_method()
        assert result == "secret"

    @pytest.mark.asyncio
    async def test_value_error_tracked_as_value_error(self):
        mock_provider = MagicMock()
        mock_provider.validate = AsyncMock(side_effect=ValueError("invalid"))
        proxy = MetricsProxy(mock_provider, "val_prim", "val_prov")

        before = ERROR_COUNT.labels(
            primitive="val_prim",
            provider="val_prov",
            method="validate",
            error_type="ValueError",
        )._value.get()

        with pytest.raises(ValueError):
            await proxy.validate()

        after = ERROR_COUNT.labels(
            primitive="val_prim",
            provider="val_prov",
            method="validate",
            error_type="ValueError",
        )._value.get()
        assert after == before + 1
