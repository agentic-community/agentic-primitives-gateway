"""Memory-specific metadata-scrubbing wrappers used by ``MemoryProvider.__init_subclass__``.

This module mirrors the pattern established in
``primitives/knowledge/_audit.py``: every subclass of ``MemoryProvider``
gets its read-path methods (``retrieve``, ``search``, ``list_memories``)
auto-wrapped to strip operator-configured denylist keys from
``MemoryRecord.metadata`` before any caller — REST, agent tools,
programmatic — sees them.

**Why just the read path?** ``store()`` is the write side — the
metadata dict the operator supplies there is what we're trying to
filter on the way *out*.  Scrubbing on write would delete fields from
the operator's own stored records, which is the opposite of the
feature.  Denylist semantics are "never expose" not "never store."

**Why no audit events here?** Unlike knowledge, ``MetricsProxy``
already emits ``provider.call`` audit events for every primitive
method, so memory inherits generic call auditing without a dedicated
wrapper.  This module carries only the scrubbing concern — audit /
metrics stay with the proxy.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from typing import Any

from agentic_primitives_gateway.primitives._metadata_scrub import apply_metadata_denylist, get_denylist


def _extract_record_metadata(record: Any) -> Iterable[dict[str, Any]]:
    """Yield the single metadata dict on a ``MemoryRecord``."""
    meta = getattr(record, "metadata", None)
    if isinstance(meta, dict):
        yield meta


def _extract_search_result_metadata(result: Any) -> Iterable[dict[str, Any]]:
    """Yield the metadata dict nested inside ``SearchResult.record``."""
    record = getattr(result, "record", None)
    if record is not None:
        yield from _extract_record_metadata(record)


def wrap_retrieve(func: Any) -> Any:
    """Wrap ``retrieve`` to scrub metadata on the returned record.

    ``retrieve`` returns a single ``MemoryRecord | None``, so the
    helper sees a one-element iterable or nothing.  ``None`` results
    (missing key) are naturally skipped inside ``apply_metadata_denylist``.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = await func(*args, **kwargs)
        if result is not None:
            apply_metadata_denylist([result], get_denylist("memory"), extract=_extract_record_metadata)
        return result

    return wrapper


def wrap_search(func: Any) -> Any:
    """Wrap ``search`` to scrub metadata on each result's nested record.

    ``search`` returns ``list[SearchResult]`` where the metadata lives
    under ``result.record.metadata`` — the extractor handles the
    nested reach-in so call sites don't need to know the shape.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        results = await func(*args, **kwargs)
        apply_metadata_denylist(results, get_denylist("memory"), extract=_extract_search_result_metadata)
        return results

    return wrapper


def wrap_list_memories(func: Any) -> Any:
    """Wrap ``list_memories`` to scrub metadata on every record in the list."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        records = await func(*args, **kwargs)
        apply_metadata_denylist(records, get_denylist("memory"), extract=_extract_record_metadata)
        return records

    return wrapper
