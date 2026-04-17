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
