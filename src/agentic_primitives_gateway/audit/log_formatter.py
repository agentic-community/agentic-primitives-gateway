"""Structured JSON log formatter and a secret-scrubbing logging filter.

These are opt-in: ``main.py`` installs them when ``settings.logging.format``
is ``"json"`` and/or ``settings.logging.sanitize`` is true.  They apply to
application logs only — audit events go through :class:`AuditRouter` and
are *not* rendered via Python ``logging``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from agentic_primitives_gateway.audit.redaction import scrub_secrets


class JsonLogFormatter(logging.Formatter):
    """Render each log record as a single JSON object.

    Every record carries the standard ``LogRecord`` fields plus any
    ``request_id``, ``correlation_id``, ``principal_id``, ``principal_type``
    attributes attached by the log record factory in ``main.py``.  Extra
    keys on the record (via ``logger.info(msg, extra={...})``) are
    preserved under ``extra``.
    """

    _STD_KEYS: frozenset[str] = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "asctime",
            "taskName",
            "message",
            # Record-factory additions handled explicitly below.
            "request_id",
            "correlation_id",
            "principal_id",
            "principal_type",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "message": record.getMessage(),
        }

        for key in ("request_id", "correlation_id", "principal_id", "principal_type"):
            value = getattr(record, key, None)
            if value not in (None, "", "-"):
                payload[key] = value

        extra: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in self._STD_KEYS or key.startswith("_"):
                continue
            extra[key] = value
        if extra:
            payload["extra"] = extra

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class LogSanitizationFilter(logging.Filter):
    """Scrub known secret patterns from rendered log messages.

    Runs before the formatter sees the record.  Mutates ``record.msg``
    (and resolves any ``record.args`` by rendering first) so that by the
    time the formatter serializes, no secret substring remains.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # If rendering itself fails, pass the record through unchanged
            # so we don't silently swallow diagnostic information.
            return True
        sanitized = scrub_secrets(message)
        if sanitized != message:
            record.msg = sanitized
            record.args = None
        return True
