"""Extended health check tests covering _check_provider and readiness edge cases."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from agentic_primitives_gateway.routes.health import _check_provider


class TestCheckProvider:
    async def test_healthy_provider(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.healthcheck.return_value = True

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_reg:
            mock_reg.get_primitive.return_value = mock_prim
            prim, prov, key, healthy = await _check_provider("memory", "default")

        assert prim == "memory"
        assert prov == "default"
        assert key == "memory/default"
        assert healthy is True

    async def test_unhealthy_provider(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.healthcheck.return_value = False

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_reg:
            mock_reg.get_primitive.return_value = mock_prim
            _, _, _, healthy = await _check_provider("memory", "default")

        assert healthy is False

    async def test_provider_healthcheck_exception(self) -> None:
        mock_provider = AsyncMock()
        mock_provider.healthcheck.side_effect = RuntimeError("fail")

        mock_prim = MagicMock()
        mock_prim.get.return_value = mock_provider

        with patch("agentic_primitives_gateway.routes.health.registry") as mock_reg:
            mock_reg.get_primitive.return_value = mock_prim
            _, _, _, healthy = await _check_provider("memory", "default")

        assert healthy is False
