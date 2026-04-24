from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType

if TYPE_CHECKING:
    from agentic_primitives_gateway.registry import ProviderRegistry

logger = logging.getLogger(__name__)

_last_reload_error: str | None = None


def get_last_reload_error() -> str | None:
    """Return the last reload error message, or None if the last reload succeeded."""
    return _last_reload_error


class ConfigWatcher:
    """Polls a config file for changes and triggers provider reload.

    Uses ``os.stat()`` to detect mtime/inode changes, which correctly
    follows Kubernetes ConfigMap symlink chains (kubelet swaps the
    ``..data`` symlink on update, changing the inode).
    """

    def __init__(
        self,
        config_path: str,
        registry: ProviderRegistry,
        poll_interval: float = 5.0,
    ) -> None:
        self._config_path = config_path
        self._registry = registry
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._stat_key_cache: tuple[float, int] | None = None

    def _stat_key(self) -> tuple[float, int] | None:
        """Return ``(mtime, inode)`` for the resolved config file path."""
        try:
            st = Path(self._config_path).resolve().stat()
            return (st.st_mtime, st.st_ino)
        except OSError:
            return None

    async def _reload(self) -> None:
        global _last_reload_error
        started = time.monotonic()
        try:
            from agentic_primitives_gateway.config import Settings

            new_settings = Settings.load()
            await self._registry.reload(new_settings)
            _last_reload_error = None
            duration_ms = (time.monotonic() - started) * 1000
            logger.info("Config reloaded successfully from %s", self._config_path)
            emit_audit_event(
                action=AuditAction.CONFIG_RELOAD,
                outcome=AuditOutcome.SUCCESS,
                resource_type=ResourceType.CONFIG,
                resource_id=self._config_path,
                duration_ms=duration_ms,
                metadata={"config_path": self._config_path},
            )
        except Exception as e:
            msg = f"Config reload failed for {self._config_path}"
            logger.exception(msg)
            _last_reload_error = msg
            duration_ms = (time.monotonic() - started) * 1000
            emit_audit_event(
                action=AuditAction.CONFIG_RELOAD,
                outcome=AuditOutcome.FAILURE,
                resource_type=ResourceType.CONFIG,
                resource_id=self._config_path,
                reason="reload_failed",
                duration_ms=duration_ms,
                metadata={
                    "config_path": self._config_path,
                    "error_type": type(e).__name__,
                },
            )

    async def _poll_loop(self) -> None:
        self._stat_key_cache = self._stat_key()
        while True:
            await asyncio.sleep(self._poll_interval)
            new_key = self._stat_key()
            if new_key != self._stat_key_cache:
                logger.info(
                    "Config file change detected: %s (was %s, now %s)",
                    self._config_path,
                    self._stat_key_cache,
                    new_key,
                )
                self._stat_key_cache = new_key
                await self._reload()

    async def start(self) -> None:
        """Start the background polling task."""
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Config watcher started (path=%s, interval=%.1fs)",
            self._config_path,
            self._poll_interval,
        )

    async def stop(self) -> None:
        """Cancel the background polling task."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
            logger.info("Config watcher stopped")
