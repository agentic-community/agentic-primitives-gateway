from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from agentic_primitives_gateway.main import app
from agentic_primitives_gateway.registry import registry


class TestCodeInterpreterExtendedRoutes:
    """Tests for execute error, upload_file, and download_file routes."""

    def setup_method(self):
        from agentic_primitives_gateway.config import Settings

        test_settings = Settings(
            providers={
                "memory": {"backend": "agentic_primitives_gateway.primitives.memory.in_memory.InMemoryProvider"},
                "observability": {
                    "backend": "agentic_primitives_gateway.primitives.observability.noop.NoopObservabilityProvider"
                },
                "llm": {"backend": "agentic_primitives_gateway.primitives.llm.noop.NoopLLMProvider"},
                "tools": {"backend": "agentic_primitives_gateway.primitives.tools.noop.NoopToolsProvider"},
                "identity": {"backend": "agentic_primitives_gateway.primitives.identity.noop.NoopIdentityProvider"},
                "code_interpreter": {
                    "backend": "agentic_primitives_gateway.primitives.code_interpreter.noop.NoopCodeInterpreterProvider"
                },
                "browser": {"backend": "agentic_primitives_gateway.primitives.browser.noop.NoopBrowserProvider"},
            }
        )
        registry.initialize(test_settings)
        self.client = TestClient(app, raise_server_exceptions=False)

    def _patch_ci(self, method_name, **kwargs):
        return patch.object(
            registry.get_primitive("code_interpreter").get()._provider,
            method_name,
            new_callable=AsyncMock,
            **kwargs,
        )

    # ── execute error ─────────────────────────────────────────────────

    def test_execute_session_not_found_returns_404(self):
        with self._patch_ci("execute", side_effect=ValueError("Session X not found")):
            resp = self.client.post(
                "/api/v1/code-interpreter/sessions/X/execute",
                json={"code": "print(1)"},
            )
            assert resp.status_code == 404
            assert "not found" in resp.json()["detail"]

    def test_execute_success(self):
        with self._patch_ci(
            "execute",
            return_value={"session_id": "s1", "stdout": "1\n", "stderr": "", "exit_code": 0},
        ):
            resp = self.client.post(
                "/api/v1/code-interpreter/sessions/s1/execute",
                json={"code": "print(1)"},
            )
            assert resp.status_code == 200
            assert resp.json()["stdout"] == "1\n"

    # ── upload_file ───────────────────────────────────────────────────

    def test_upload_file_success(self):
        with self._patch_ci(
            "upload_file",
            return_value={"filename": "data.csv", "size": 10, "session_id": "s1"},
        ):
            resp = self.client.post(
                "/api/v1/code-interpreter/sessions/s1/files",
                files={"file": ("data.csv", b"0123456789", "text/csv")},
            )
            assert resp.status_code == 200
            assert resp.json()["filename"] == "data.csv"

    def test_upload_file_session_not_found_returns_404(self):
        with self._patch_ci("upload_file", side_effect=ValueError("Session X not found")):
            resp = self.client.post(
                "/api/v1/code-interpreter/sessions/X/files",
                files={"file": ("test.txt", b"hello", "text/plain")},
            )
            assert resp.status_code == 404

    # ── download_file ─────────────────────────────────────────────────

    def test_download_file_success(self):
        with self._patch_ci("download_file", return_value=b"file content here"):
            resp = self.client.get("/api/v1/code-interpreter/sessions/s1/files/output.txt")
            assert resp.status_code == 200
            assert resp.content == b"file content here"
            assert "attachment" in resp.headers.get("content-disposition", "")

    def test_download_file_session_not_found_returns_404(self):
        with self._patch_ci("download_file", side_effect=ValueError("Session X not found")):
            resp = self.client.get("/api/v1/code-interpreter/sessions/X/files/out.txt")
            assert resp.status_code == 404
