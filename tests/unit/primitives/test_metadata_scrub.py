"""Tests for the shared metadata-scrubbing helper.

The helper is intentionally shape-agnostic — callers supply an
``extract`` function that yields the metadata dicts to scrub on each
object.  These tests cover the helper in isolation; per-primitive
tests (``knowledge/test_citations.py``, ``memory/test_metadata_denylist.py``)
exercise the wiring through each primitive's own ``_audit.py``
wrapper and the single ``Settings.metadata_denylists`` dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from agentic_primitives_gateway.primitives._metadata_scrub import (
    apply_metadata_denylist,
    get_denylist,
    scrub_dict,
)


@dataclass
class _RecordLike:
    """Minimal shape with a ``metadata`` dict — mimics MemoryRecord / RetrievedChunk."""

    metadata: dict[str, Any] = field(default_factory=dict)


class TestScrubDict:
    def test_removes_denylisted_keys(self) -> None:
        d = {"keep": 1, "drop_me": "secret", "also_drop": True}
        scrub_dict(d, ["drop_me", "also_drop"])
        assert d == {"keep": 1}

    def test_empty_target_is_noop(self) -> None:
        d: dict[str, Any] = {}
        scrub_dict(d, ["anything"])
        assert d == {}

    def test_empty_denylist_is_noop(self) -> None:
        d = {"keep": 1}
        scrub_dict(d, [])
        assert d == {"keep": 1}

    def test_non_matching_keys_survive(self) -> None:
        d = {"alpha": 1, "beta": 2}
        scrub_dict(d, ["gamma"])
        assert d == {"alpha": 1, "beta": 2}

    def test_is_top_level_only(self) -> None:
        """Contract: nested dicts are NOT recursed.  Operators get one
        clear guarantee — "these keys, at the top level, never leave."
        Recursion would make the scrub scope unpredictable and hard to
        test safely across primitive shapes.
        """
        d = {"outer": {"inner_drop": "still here"}, "drop_top": 1}
        scrub_dict(d, ["drop_top", "inner_drop"])
        assert d == {"outer": {"inner_drop": "still here"}}


class TestApplyMetadataDenylist:
    def test_applies_across_collection(self) -> None:
        records = [
            _RecordLike(metadata={"source": "a", "internal": "x"}),
            _RecordLike(metadata={"source": "b", "internal": "y"}),
        ]
        apply_metadata_denylist(records, ["internal"], extract=lambda r: [r.metadata])
        assert all("internal" not in r.metadata for r in records)
        assert [r.metadata["source"] for r in records] == ["a", "b"]

    def test_empty_denylist_short_circuits(self) -> None:
        """Wrappers call this unconditionally; empty denylist must do
        no work.  Passing an extractor that would raise if called
        proves the short-circuit path is real, not just incidental.
        """
        rec = _RecordLike(metadata={"x": 1})

        def boom(_: Any) -> list[dict[str, Any]]:
            raise AssertionError("extractor must not be called when denylist is empty")

        apply_metadata_denylist([rec], [], extract=boom)
        apply_metadata_denylist([rec], None, extract=boom)

    def test_none_objects_is_noop(self) -> None:
        apply_metadata_denylist(None, ["drop"], extract=lambda x: [])
        # No assertion needed — the call must not raise.

    def test_extractor_can_yield_multiple_dicts_per_object(self) -> None:
        """Important for knowledge: a chunk has its own ``metadata``
        AND ``citations[].metadata``.  The extractor yields each dict
        and the helper scrubs them all with the same denylist.
        """

        @dataclass
        class _Chunk:
            metadata: dict[str, Any]
            citations: list[_RecordLike]

        chunk = _Chunk(
            metadata={"top": "x", "drop": 1},
            citations=[_RecordLike(metadata={"nested": "y", "drop": 2})],
        )

        def extract_all(c: _Chunk) -> list[dict[str, Any]]:
            return [c.metadata, *[cit.metadata for cit in c.citations]]

        apply_metadata_denylist([chunk], ["drop"], extract=extract_all)
        assert chunk.metadata == {"top": "x"}
        assert chunk.citations[0].metadata == {"nested": "y"}

    def test_object_with_missing_metadata_is_skipped_gracefully(self) -> None:
        """The extractor is responsible for filtering out non-dict
        metadata (via ``isinstance`` checks), so passing something the
        extractor rejects for one object must not abort scrubbing for
        others in the batch.
        """
        good = _RecordLike(metadata={"source": "a", "drop": 1})
        bad = object()  # no ``metadata`` attr at all

        def extract(obj: Any) -> list[dict[str, Any]]:
            meta = getattr(obj, "metadata", None)
            return [meta] if isinstance(meta, dict) else []

        apply_metadata_denylist([bad, good], ["drop"], extract=extract)
        assert "drop" not in good.metadata


class TestGetDenylist:
    """``get_denylist`` is the single chokepoint new primitives call —
    the dict-based config is what makes the whole pattern extensible
    without touching ``Settings`` per primitive.
    """

    def test_returns_configured_list(self) -> None:
        with patch("agentic_primitives_gateway.config.settings") as mock_settings:
            mock_settings.metadata_denylists = {"knowledge": ["internal_url"]}
            assert get_denylist("knowledge") == ["internal_url"]

    def test_unknown_primitive_returns_empty(self) -> None:
        """New primitives don't have entries by default — the lookup
        must return ``[]`` rather than raise.  This is what makes
        opting in cost zero: the wrapper runs, finds nothing to scrub,
        and proceeds.
        """
        with patch("agentic_primitives_gateway.config.settings") as mock_settings:
            mock_settings.metadata_denylists = {"knowledge": ["x"]}
            assert get_denylist("brand_new_primitive") == []

    def test_missing_metadata_denylists_attr_returns_empty(self) -> None:
        """Old configs without the field at all must not break — the
        lookup defaults to empty and scrubbing is a no-op.
        """
        with patch("agentic_primitives_gateway.config.settings") as mock_settings:
            # Simulate an older Settings instance without the field.
            del mock_settings.metadata_denylists
            mock_settings.metadata_denylists = None  # recreate as None to exercise the fallback
            assert get_denylist("knowledge") == []

    def test_returns_a_fresh_list(self) -> None:
        """Callers iterate the list, so returning a live reference
        would risk mutation.  Contract: each call returns a new list.
        """
        with patch("agentic_primitives_gateway.config.settings") as mock_settings:
            mock_settings.metadata_denylists = {"knowledge": ["x", "y"]}
            a = get_denylist("knowledge")
            b = get_denylist("knowledge")
            assert a == b and a is not b
