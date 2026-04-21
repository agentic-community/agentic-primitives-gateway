"""File-backed persistence mixin for the versioned spec store.

Concrete file-backed agent and team stores compose :class:`FileSpecStore`
with the spec-specific logic in ``store.py`` / ``team_store.py``.  All
state is kept in memory and written atomically on every mutation via a
tmp-file + rename.  Dev-only: not safe for multi-replica deployments.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from uuid import NAMESPACE_OID, uuid5

from agentic_primitives_gateway.agents.base_store import (
    _StoreState,
    migrate_legacy_mapping,
)

logger = logging.getLogger(__name__)


class FileSpecStore:
    """File-backed persistence — supplies ``_load_state`` / ``_save_state``.

    Expected attributes on the composed class:

    * ``_entity_label``         — e.g. ``"agent"`` / ``"team"`` (for logs)
    * ``_version_name_field``   — the version model's name field key
    * ``_migration_namespace_oid`` — stable string used as a UUIDv5
      namespace for legacy migration so reruns are idempotent.
    """

    _entity_label: str
    _migration_namespace_oid: str
    _version_name_field: str

    def __init__(self, path: str = "spec.json") -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._state = self._load_from_disk()

    def _load_from_disk(self) -> _StoreState:
        if not self._path.exists():
            return _StoreState()
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            logger.exception("Failed to parse %s; starting empty", self._path)
            return _StoreState()
        if isinstance(data, dict) and "versions" in data:
            return _StoreState.from_json(data)
        # Legacy layout — leave state empty; ``migrate_from_legacy`` will
        # populate it from the same file.
        return _StoreState()

    async def _load_state(self) -> _StoreState:
        return self._state

    async def _save_state(self, state: _StoreState) -> None:
        with self._lock:
            self._state = state
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(state.to_json(), indent=2, default=str))
            tmp.replace(self._path)

    async def migrate_from_legacy(self) -> int:
        """One-shot migration from the legacy ``{name: spec_dict}`` layout."""
        if not self._path.exists():
            return 0
        try:
            raw = json.loads(self._path.read_text())
        except Exception:
            return 0
        if not isinstance(raw, dict) or "versions" in raw:
            return 0
        migrated = migrate_legacy_mapping(
            raw,
            state=self._state,
            version_cls_name=self._entity_label,
            migration_ns=uuid5(NAMESPACE_OID, self._migration_namespace_oid),
            version_name_field=self._version_name_field,
        )
        await self._save_state(self._state)
        return migrated
