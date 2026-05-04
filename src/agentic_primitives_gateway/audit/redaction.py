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
        "x-aws-access-key-id",
    }
)

# Key prefixes whose values are always redacted.  ``X-Cred-*`` is the
# per-request service-credential convention (``X-Cred-Keycloak-Client-Secret``,
# ``X-Cred-Langfuse-Public-Key``, etc.) — new backends add new key names
# without updating this file, so a prefix match is required for coverage
# to stay honest as the credential surface grows.  ``X-AWS-*`` catches
# any future AWS header we might add (``X-AWS-Role-Arn`` etc.); the only
# AWS header that is *not* sensitive (``X-AWS-Region``) is explicitly
# allowed below.
REDACT_KEY_PREFIXES: tuple[str, ...] = ("x-cred-", "x-aws-")
REDACT_KEY_PREFIX_ALLOW: frozenset[str] = frozenset({"x-aws-region"})

# Regexes for known secret shapes in free-form log text.  These fire on
# application logs and exception tracebacks (via
# ``LogSanitizationFilter``), catching the string form a credential might
# appear in when a downstream library raises an error containing the
# original request header.
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Bearer tokens
    re.compile(r"Bearer\s+[A-Za-z0-9\-_\.=]+", re.IGNORECASE),
    # AWS access key IDs (prefix literal; the actual ID format is 16-char alnum)
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # JWT three-part tokens (rough match — three base64url sections)
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    # apg.<service>.<key> = <value>
    re.compile(r"apg\.[a-z_]+\.[a-z_]+\s*[:=]\s*\S+", re.IGNORECASE),
    # X-Cred-<Service>-<Key>: <value> — scrubs the value after the colon
    # (or equals sign).  Matches through to end-of-line / next whitespace.
    re.compile(r"(?i)(X-Cred-[A-Za-z0-9\-]+)\s*[:=]\s*\S+"),
    # X-AWS-Secret-Access-Key: <40-ish chars> and friends.  ``X-AWS-Region``
    # is excluded on purpose; it isn't a secret.
    re.compile(r"(?i)(X-AWS-Secret-Access-Key|X-AWS-Session-Token|X-AWS-Access-Key-Id)\s*[:=]\s*\S+"),
    # Raw ``Authorization: <opaque-token>`` without the ``Bearer`` prefix.
    # The Bearer variant is already handled above; this catches opaque
    # API keys passed in the Authorization header.
    re.compile(r"(?im)^\s*Authorization\s*:\s*(?!Bearer\b)\S+"),
)


def _is_redacted_key(key: str) -> bool:
    """True if ``key`` matches the default deny-list or a redaction prefix."""
    low = key.lower()
    if low in DEFAULT_REDACT_KEYS:
        return True
    if low in REDACT_KEY_PREFIX_ALLOW:
        return False
    return any(low.startswith(p) for p in REDACT_KEY_PREFIXES)


def redact_mapping(
    data: dict[str, Any],
    extra_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a shallow-redacted copy of ``data``.

    Keys matching the default deny-list, an ``X-Cred-*`` / ``X-AWS-*``
    prefix (with ``X-AWS-Region`` allowed through), or ``extra_keys``
    (case-insensitive) have their values replaced with ``"***"``.
    Nested dicts are walked recursively.  Lists/tuples are returned
    unchanged — the caller is responsible for not putting secrets into
    list-valued metadata.
    """
    extra: frozenset[str] = frozenset({k.lower() for k in extra_keys})
    result = _redact(data, extra)
    assert isinstance(result, dict)
    return result


def _redact(value: Any, extra: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {
            k: (REDACTED if (_is_redacted_key(k) or k.lower() in extra) else _redact(v, extra))
            for k, v in value.items()
        }
    return value


def scrub_secrets(text: str) -> str:
    """Replace substrings matching any :data:`SECRET_PATTERNS` with ``"***"``."""
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text
