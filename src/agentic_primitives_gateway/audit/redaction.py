"""Redaction helpers for audit events and log records.

Two responsibilities:

* ``redact_mapping`` walks a dict and replaces values for keys matching
  a deny-list with ``"***"``.  Used by :func:`emit_audit_event` on the
  ``metadata`` payload before constructing the event.
* ``SECRET_PATTERNS`` is a list of regexes matching known secret shapes
  (Bearer tokens, AWS access keys, JWTs, ``apg.*`` key=value pairs).
  Used by ``LogSanitizationFilter`` to scrub application log output.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REDACTED = "***"

# Default keys whose values are always redacted inside audit event metadata.
DEFAULT_REDACT_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "secret",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "x-aws-secret-access-key",
        "x-aws-session-token",
    }
)

# Regexes for known secret shapes in free-form log text.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Bearer tokens
    re.compile(r"Bearer\s+[A-Za-z0-9\-_\.=]+", re.IGNORECASE),
    # AWS access key IDs
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # JWT three-part tokens (rough match — three base64url sections)
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    # apg.<service>.<key> = <value>
    re.compile(r"apg\.[a-z_]+\.[a-z_]+\s*[:=]\s*\S+", re.IGNORECASE),
)


def redact_mapping(
    data: dict[str, Any],
    extra_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a shallow-redacted copy of ``data``.

    Keys matching the default deny-list or ``extra_keys`` (case-insensitive)
    have their values replaced with ``"***"``.  Nested dicts are walked
    recursively.  Lists/tuples are returned unchanged — the caller is
    responsible for not putting secrets into list-valued metadata.
    """
    deny: frozenset[str] = frozenset({k.lower() for k in extra_keys}) | DEFAULT_REDACT_KEYS
    result = _redact(data, deny)
    assert isinstance(result, dict)
    return result


def _redact(value: Any, deny: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if k.lower() in deny else _redact(v, deny)) for k, v in value.items()}
    return value


def scrub_secrets(text: str) -> str:
    """Replace substrings matching any :data:`SECRET_PATTERNS` with ``"***"``."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text
