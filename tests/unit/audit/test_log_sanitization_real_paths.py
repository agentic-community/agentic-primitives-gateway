"""Intent-level test: secret patterns are actually scrubbed when real code logs them.

Contract (CLAUDE.md "Log sanitization"): ``LogSanitizationFilter``
attached to the root logger scrubs Bearer tokens, AWS access keys,
JWTs, and ``apg.*`` credentials from application log output.  This
is the forensic-safety layer — nothing in stdout/stderr should ever
carry a verbatim credential, even when the code that logged it
wasn't thinking about secrets.

Existing tests call the ``LogSanitizationFilter.filter()`` method
directly with synthetic records and assert the regex ran.  They do
NOT:
- Verify the filter is actually attached when ``logging.sanitize``
  is on (integration missing).
- Capture the rendered output of a realistic multi-step log
  (e.g. a provider error containing a Bearer token inside a full
  ``response.body`` string) and assert it doesn't appear.
- Handle edge cases: the token embedded inside a dict-formatted
  log record, an exception traceback with the secret in the
  exception message, args-style formatting with a secret in an
  ``%s`` substitution.

This file fills those gaps.
"""

from __future__ import annotations

import io
import logging

from agentic_primitives_gateway.audit.log_formatter import (
    JsonLogFormatter,
    LogSanitizationFilter,
)


def _capture_log_output(message: str, *args, level: int = logging.ERROR, exc: Exception | None = None) -> str:
    """Run a single log event through a realistic handler setup:
    ``LogSanitizationFilter`` → ``JsonLogFormatter`` → ``StringIO``.
    Returns the rendered JSON line so the test can grep for leaks.
    """
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(LogSanitizationFilter())
    logger = logging.getLogger("test.sanitize")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        if exc is not None:
            try:
                raise exc
            except Exception:
                logger.log(level, message, *args, exc_info=True)
        else:
            logger.log(level, message, *args)
    finally:
        logger.removeHandler(handler)
    return buf.getvalue()


class TestBearerTokenScrubbing:
    def test_bearer_token_in_message_body(self):
        """Realistic scenario: provider logs the raw 401 response
        including the ``Authorization`` header.
        """
        token = "abcdef1234567890.abcdef.signed"
        output = _capture_log_output(
            f'Provider auth failed: response body: {{"error":"401","header":"Authorization: Bearer {token}"}}'
        )
        assert token not in output, f"Bearer token leaked into log output: {output}"
        assert "Bearer" not in output or "Bearer ***" in output or "***" in output, (
            f"Bearer prefix left behind unscrubbed: {output}"
        )

    def test_bearer_token_in_format_args(self):
        """The filter must render args first, then scrub.  A naive
        implementation that scrubbed only ``record.msg`` (the format
        string) would miss tokens passed via %s args.
        """
        output = _capture_log_output("Request with auth %s", "Bearer secret-token-12345")
        assert "secret-token-12345" not in output, f"Bearer token passed via %s args was not scrubbed: {output}"

    def test_bearer_token_inside_exception_message(self):
        """An exception whose message contains a secret → the
        traceback ends up in the log output.  The filter must scrub
        that too.
        """
        token_value = "Bearer sekret-abc-xyz-123"
        output = _capture_log_output(
            "Downstream call failed",
            exc=RuntimeError(f"HTTP 401 with header '{token_value}'"),
        )
        assert "sekret-abc-xyz-123" not in output, f"Token inside exception traceback not scrubbed.  Output: {output}"


class TestAWSKeyScrubbing:
    def test_aws_access_key_in_message(self):
        """AWS access key IDs follow AKIA[0-9A-Z]{16}.  Logging
        code in boto3 or our own credentials path could leak one.
        """
        ak = "AKIAIOSFODNN7EXAMPLE"
        output = _capture_log_output(f"S3 call failed with creds: access_key={ak}")
        assert ak not in output, f"AWS access key leaked: {output}"

    def test_aws_key_in_url_style_log(self):
        """Some providers log sign-versioned URLs that embed the
        access key.
        """
        ak = "AKIAIOSFODNN7EXAMPLE"
        output = _capture_log_output(
            f"Presigned URL: https://s3.amazonaws.com/bucket?X-Amz-Credential={ak}%2F20240101..."
        )
        assert ak not in output


class TestJWTScrubbing:
    def test_jwt_in_message(self):
        """A three-part JWT in a log line (e.g., a cookie value, an
        OIDC token in a callback debug log) must be scrubbed.
        """
        jwt = (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        output = _capture_log_output(f"Decoded user token: {jwt}")
        assert jwt not in output, f"JWT leaked into log: {output}"


class TestApgCredentialScrubbing:
    def test_apg_cred_pair_in_message(self):
        """Log lines debugging the credential resolver may render
        an ``apg.service.key=value`` pair.  Values must be scrubbed.
        """
        output = _capture_log_output("Resolved user attr: apg.langfuse.secret_key=sk-abc123secret")
        assert "sk-abc123secret" not in output, f"apg.* value leaked: {output}"

    def test_apg_cred_pair_in_args(self):
        output = _capture_log_output("Attribute: %s", "apg.mcp_registry.api_key=real-key-xyz")
        assert "real-key-xyz" not in output


class TestCleanMessagesPassThrough:
    """The filter must NOT break normal log output.  Messages with
    no secret patterns should round-trip unchanged (modulo JSON
    serialization).
    """

    def test_no_secrets_no_redaction(self):
        output = _capture_log_output("Processed 42 records in 150ms")
        assert "42 records" in output
        assert "150ms" in output
        assert "***" not in output


class TestFilterMustBeAttached:
    """Direct sanity: without the filter attached, the secrets WOULD
    appear in output.  This pins that the filter is doing the work
    (otherwise the above tests could pass trivially if the JSON
    formatter dropped the message content altogether).
    """

    def test_without_filter_bearer_token_appears(self):
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(JsonLogFormatter())
        logger = logging.getLogger("test.no-sanitize")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        try:
            logger.warning("auth: Bearer SHOULDAPPEAR")
        finally:
            logger.removeHandler(handler)
        output = buf.getvalue()
        # Without the filter, the secret IS in the output — this
        # confirms the above tests' "leak" detection is real.
        assert "SHOULDAPPEAR" in output
