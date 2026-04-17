from __future__ import annotations

import io
import json
import logging

from agentic_primitives_gateway.audit.log_formatter import JsonLogFormatter, LogSanitizationFilter


def _make_record(msg: str, *args: object, level: int = logging.INFO) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )
    return record


class TestJsonLogFormatter:
    def test_renders_standard_fields(self):
        fmt = JsonLogFormatter()
        record = _make_record("hello %s", "world")
        payload = json.loads(fmt.format(record))
        assert payload["message"] == "hello world"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"
        assert "timestamp" in payload

    def test_includes_context_fields_when_set(self):
        fmt = JsonLogFormatter()
        record = _make_record("hi")
        record.request_id = "req-1"
        record.correlation_id = "corr-1"
        record.principal_id = "alice"
        record.principal_type = "user"
        payload = json.loads(fmt.format(record))
        assert payload["request_id"] == "req-1"
        assert payload["correlation_id"] == "corr-1"
        assert payload["principal_id"] == "alice"
        assert payload["principal_type"] == "user"

    def test_skips_empty_context_fields(self):
        fmt = JsonLogFormatter()
        record = _make_record("hi")
        record.request_id = "-"
        record.correlation_id = ""
        payload = json.loads(fmt.format(record))
        assert "request_id" not in payload or payload.get("request_id") != "-"
        assert "correlation_id" not in payload

    def test_extra_fields_serialized(self):
        fmt = JsonLogFormatter()
        record = _make_record("done")
        record.custom_field = "custom_value"  # type: ignore[attr-defined]
        payload = json.loads(fmt.format(record))
        assert payload["extra"]["custom_field"] == "custom_value"

    def test_exception_included(self):
        fmt = JsonLogFormatter()
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            import sys

            record = logging.LogRecord(
                name="t",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="oops",
                args=(),
                exc_info=sys.exc_info(),
            )
        payload = json.loads(fmt.format(record))
        assert "exception" in payload
        assert "RuntimeError" in payload["exception"]


class TestLogSanitizationFilter:
    def test_redacts_bearer_tokens_in_messages(self):
        handler_output = io.StringIO()
        logger = logging.getLogger("sanit.test.bearer")
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(handler_output)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(LogSanitizationFilter())
        logger.addHandler(handler)

        logger.info("Authorization: Bearer abc.def.ghi suffix")
        assert "Bearer" not in handler_output.getvalue()
        assert "***" in handler_output.getvalue()

    def test_redacts_aws_keys_in_args(self):
        handler_output = io.StringIO()
        logger = logging.getLogger("sanit.test.aws")
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(handler_output)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(LogSanitizationFilter())
        logger.addHandler(handler)

        logger.info("key=%s", "AKIAIOSFODNN7EXAMPLE")
        output = handler_output.getvalue()
        assert "AKIA" not in output
        assert "***" in output

    def test_passes_through_clean_messages(self):
        handler_output = io.StringIO()
        logger = logging.getLogger("sanit.test.clean")
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(handler_output)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(LogSanitizationFilter())
        logger.addHandler(handler)

        logger.info("normal request id=%s", "req-123")
        assert "req-123" in handler_output.getvalue()
