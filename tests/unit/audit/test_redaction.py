from __future__ import annotations

from agentic_primitives_gateway.audit.redaction import REDACTED, redact_mapping, scrub_secrets


class TestRedactMapping:
    def test_redacts_default_keys(self):
        out = redact_mapping(
            {
                "authorization": "Bearer abc",
                "token": "xxx",
                "safe": "ok",
            }
        )
        assert out["authorization"] == REDACTED
        assert out["token"] == REDACTED
        assert out["safe"] == "ok"

    def test_case_insensitive(self):
        out = redact_mapping({"Authorization": "Bearer abc"})
        assert out["Authorization"] == REDACTED

    def test_recurses_into_nested_dicts(self):
        out = redact_mapping({"outer": {"password": "hunter2", "keep": "yes"}})
        assert out["outer"]["password"] == REDACTED
        assert out["outer"]["keep"] == "yes"

    def test_extra_keys_honored(self):
        out = redact_mapping({"tenant_secret": "s", "tenant_name": "acme"}, extra_keys=["tenant_secret"])
        assert out["tenant_secret"] == REDACTED
        assert out["tenant_name"] == "acme"

    def test_input_not_mutated(self):
        data = {"token": "xxx"}
        redact_mapping(data)
        assert data["token"] == "xxx"


class TestScrubSecrets:
    def test_scrubs_bearer_tokens(self):
        out = scrub_secrets("Authorization: Bearer abc.def.ghi and other text")
        assert "Bearer" not in out
        assert REDACTED in out

    def test_scrubs_aws_access_key(self):
        out = scrub_secrets("credential AKIAIOSFODNN7EXAMPLE uses")
        assert "AKIA" not in out
        assert REDACTED in out

    def test_scrubs_jwt_tokens(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SIG123abc456"
        out = scrub_secrets(f"token={jwt} suffix")
        assert jwt not in out

    def test_scrubs_apg_credentials(self):
        out = scrub_secrets("loaded apg.langfuse.public_key=pk-live-xyz from config")
        assert "pk-live-xyz" not in out

    def test_leaves_non_secret_text(self):
        out = scrub_secrets("GET /api/v1/memory/my-ns returned 200")
        assert out == "GET /api/v1/memory/my-ns returned 200"


class TestCredentialHeaderCoverage:
    """Sanitization coverage for ``X-Cred-*`` / ``X-AWS-*`` / ``Authorization``.

    These headers are the per-request credential surface — a downstream
    library error that wraps the raw request must not leak the header
    value into application logs or audit events.  The dual defense:
    ``redact_mapping`` handles dict-keyed metadata (audit events);
    ``scrub_secrets`` handles free-form text (log messages + exception
    tracebacks).
    """

    def test_redact_mapping_x_cred_prefix(self):
        """Any ``x-cred-*`` key is redacted regardless of the specific service."""
        out = redact_mapping(
            {
                "x-cred-keycloak-client-secret": "abc",
                "x-cred-langfuse-public-key": "pk-live-xyz",
                "x-cred-new-service-key": "whatever",
                "user": "alice",
            }
        )
        assert out["x-cred-keycloak-client-secret"] == REDACTED
        assert out["x-cred-langfuse-public-key"] == REDACTED
        assert out["x-cred-new-service-key"] == REDACTED
        assert out["user"] == "alice"

    def test_redact_mapping_x_cred_prefix_mixed_case(self):
        out = redact_mapping({"X-Cred-Keycloak-Client-Secret": "abc"})
        assert out["X-Cred-Keycloak-Client-Secret"] == REDACTED

    def test_redact_mapping_x_aws_prefix_redacts_secret_but_not_region(self):
        """``X-AWS-Region`` is explicitly allowed through — it isn't a secret."""
        out = redact_mapping(
            {
                "x-aws-secret-access-key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "x-aws-session-token": "FQoGZXIvYXdzEJr...",
                "x-aws-region": "us-east-1",
            }
        )
        assert out["x-aws-secret-access-key"] == REDACTED
        assert out["x-aws-session-token"] == REDACTED
        assert out["x-aws-region"] == "us-east-1"

    def test_redact_mapping_extra_keys_still_honored(self):
        """Per-call ``extra_keys`` composes with the built-in deny-list."""
        out = redact_mapping({"tenant_secret": "s"}, extra_keys=["tenant_secret"])
        assert out["tenant_secret"] == REDACTED

    def test_scrub_x_cred_header_line(self):
        """A traceback-like line carrying ``X-Cred-*: value`` is scrubbed."""
        line = "httpx.HTTPError: bad creds: X-Cred-Keycloak-Client-Secret: supersecret123"
        out = scrub_secrets(line)
        assert "supersecret123" not in out
        assert REDACTED in out

    def test_scrub_x_cred_header_equals_form(self):
        """Some libraries format headers as ``key=value``; that shape is covered."""
        out = scrub_secrets("headers(X-Cred-Langfuse-Public-Key=pk-live-abc)")
        assert "pk-live-abc" not in out

    def test_scrub_aws_secret_access_key_value(self):
        """Non-AKIA AWS secret (40-char) in free-form text is scrubbed."""
        secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        out = scrub_secrets(f"error: X-AWS-Secret-Access-Key: {secret} failed")
        assert secret not in out

    def test_scrub_aws_session_token_value(self):
        token = "FQoGZXIvYXdzEJrlongopaquetokenhere"
        out = scrub_secrets(f"X-AWS-Session-Token: {token}")
        assert token not in out

    def test_scrub_authorization_non_bearer(self):
        """Opaque API key in Authorization header (no Bearer prefix) is scrubbed."""
        out = scrub_secrets("Authorization: my-opaque-api-key-12345")
        assert "my-opaque-api-key-12345" not in out
        assert REDACTED in out

    def test_scrub_bearer_authorization_still_works(self):
        """Bearer path is unchanged — the new pattern doesn't clobber the existing one."""
        out = scrub_secrets("Authorization: Bearer eyJhbGciOiJI.payload.sig")
        assert "eyJhbGciOiJI.payload.sig" not in out
