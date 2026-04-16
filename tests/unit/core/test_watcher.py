from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from agentic_primitives_gateway.watcher import ConfigWatcher, get_last_reload_error


class TestConfigWatcher:
    async def test_stat_key_returns_tuple(self, tmp_path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("providers: {}")
        watcher = ConfigWatcher(str(config_file), MagicMock())
        key = watcher._stat_key()
        assert key is not None
        assert len(key) == 2

    async def test_stat_key_missing_file(self) -> None:
        watcher = ConfigWatcher("/nonexistent/path.yaml", MagicMock())
        assert watcher._stat_key() is None

    async def test_reload_success(self, tmp_path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("providers: {}")
        mock_reg = AsyncMock()
        watcher = ConfigWatcher(str(config_file), mock_reg)

        with patch("agentic_primitives_gateway.config.Settings.load", return_value=MagicMock()):
            await watcher._reload()

        mock_reg.reload.assert_awaited_once()

    async def test_reload_failure_sets_error(self, tmp_path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("providers: {}")
        mock_reg = AsyncMock()
        watcher = ConfigWatcher(str(config_file), mock_reg)

        with patch("agentic_primitives_gateway.config.Settings.load", side_effect=ValueError("bad config")):
            await watcher._reload()

        assert get_last_reload_error() is not None

    async def test_start_and_stop(self, tmp_path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("providers: {}")
        watcher = ConfigWatcher(str(config_file), MagicMock(), poll_interval=100)
        await watcher.start()
        assert watcher._task is not None
        # Starting again is a no-op
        await watcher.start()
        await watcher.stop()
        assert watcher._task is None
