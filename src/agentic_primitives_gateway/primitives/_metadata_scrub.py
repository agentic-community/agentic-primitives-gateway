"""Shared helper: strip denylisted keys from metadata dicts on primitive results.

This is the mechanism used by per-primitive ABC audit wrappers (see
``primitives/knowledge/_audit.py`` and ``primitives/memory/_audit.py``)
to enforce operator-configured metadata scrubbing *uniformly* — before
any downstream consumer (REST response, agent tool, audit metadata)
sees the value.

The helper is deliberately shape-agnostic: callers identify which
attribute paths to scrub on each result object.  That keeps the helper
reusable across primitives with very different return shapes without
pulling in shape knowledge here.

**Why this lives as a shared helper, not generic MetricsProxy scrub:**
MetricsProxy wraps every primitive method but is shape-unaware.  A
generic walker would need per-type ``isinstance`` checks or a fragile
"any field called metadata" heuristic — silent misses on new shapes,
and no way to recurse into nested structures like
``RetrievedChunk.citations[].metadata``.  Per-primitive wrappers know
their own shape and call this helper with explicit extractors.

**Contract note:** scrubbing is by convention, not contract — a new
primitive whose author forgets to wrap silently leaks operator-attached
metadata.  The "Adding a New Provider" section of CLAUDE.md calls this
out so future primitives pick up the pattern.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

# Signature for a function that yields every ``dict[str, Any]`` a caller
# wants scrubbed on one result object.  Callers supply this because only
# they know the shape — e.g. a knowledge chunk has ``.metadata`` plus
# ``.citations[].metadata``; a memory record has just ``.metadata``.
MetadataExtractor = Callable[[Any], Iterable[dict[str, Any]]]


def get_denylist(primitive: str) -> list[str]:
    """Look up the operator-configured metadata denylist for a primitive.

    Reads ``Settings.metadata_denylists[primitive]`` lazily so per-
    primitive ``_audit.py`` wrappers don't have to import the settings
    graph eagerly (primitives are imported very early in app bootstrap).

    Returns ``[]`` when no entry exists — no-op scrubbing.  New
    primitives that want to opt into the pattern add a key to the
    single config dict; they don't add a field on ``Settings`` or a
    new class in ``config.py``.
    """
    try:
        from agentic_primitives_gateway.config import settings

        denylists = getattr(settings, "metadata_denylists", None) or {}
        return list(denylists.get(primitive, []))
    except Exception:
        return []


def scrub_dict(target: dict[str, Any], denylist: Iterable[str]) -> None:
    """Pop every denylisted top-level key from ``target`` in place.

    Top-level only — nested dicts are not recursed.  Empty / None
    targets and empty denylists are no-ops.  Matching the behavior to
    a shallow scrub keeps the guarantee easy to reason about for
    operators: "these exact keys, at the top level, never leave."
    """
    if not target or not denylist:
        return
    deny_set = set(denylist)
    if not deny_set:
        return
    for key in deny_set.intersection(target.keys()):
        target.pop(key, None)


def apply_metadata_denylist(
    objects: Iterable[Any] | None,
    denylist: list[str] | None,
    *,
    extract: MetadataExtractor,
) -> None:
    """Scrub every metadata dict yielded by ``extract`` on each object.

    ``extract(obj)`` yields zero or more ``dict[str, Any]`` instances
    the caller wants scrubbed on ``obj``.  Each dict is scrubbed in
    place against the denylist.  Scrubbing runs per-object so a single
    wrapper can cover multiple result shapes without caring whether the
    primitive returned a ``list`` or a single value.

    Empty / ``None`` denylists and empty result sets are no-ops, so
    wrappers can call this unconditionally without guarding.
    """
    if not denylist or objects is None:
        return
    deny_set = set(denylist)
    if not deny_set:
        return
    for obj in objects:
        if obj is None:
            continue
        for metadata in extract(obj):
            scrub_dict(metadata, deny_set)
