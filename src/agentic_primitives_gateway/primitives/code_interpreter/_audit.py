"""Code-interpreter audit wrappers used by ``CodeInterpreterProvider.__init_subclass__``.

Every subclass gets ``execute`` / ``upload_file`` / ``download_file``
wrapped automatically to emit ``code_interpreter.execute`` /
``code_interpreter.file.upload`` / ``code_interpreter.file.download``
audit events at the provider boundary — so agent-tool invocations of
code_interpreter produce the same specific event a REST call would.

Code bodies are intentionally excluded from metadata (treat as
sensitive); only size / language / session id land in the event.
"""

from __future__ import annotations

import functools
from typing import Any

from agentic_primitives_gateway.audit.emit import emit_audit_event
from agentic_primitives_gateway.audit.models import AuditAction, AuditOutcome, ResourceType
from agentic_primitives_gateway.models.enums import CodeLanguage


def _emit(action: str, outcome: AuditOutcome, *, resource_id: str | None, metadata: dict[str, Any]) -> None:
    # See tools/_audit.py::_emit for the ``layer`` rationale.
    metadata.setdefault("layer", "primitive")
    emit_audit_event(
        action=action,
        outcome=outcome,
        resource_type=ResourceType.CODE_EXECUTION,
        resource_id=resource_id,
        metadata=metadata,
    )


def wrap_execute(func: Any) -> Any:
    """Wrap ``execute`` to emit ``code_interpreter.execute``."""

    @functools.wraps(func)
    async def wrapper(
        self: Any,
        session_id: str,
        code: str,
        language: str = CodeLanguage.PYTHON,
    ) -> Any:
        code_length = len(code) if code is not None else 0
        try:
            result = await func(self, session_id, code, language)
        except Exception as exc:
            _emit(
                AuditAction.CODE_EXECUTE,
                AuditOutcome.ERROR,
                resource_id=session_id,
                metadata={
                    "session_id": session_id,
                    "language": str(language),
                    "code_length": code_length,
                    "error_type": type(exc).__name__,
                },
            )
            raise

        stdout_length = 0
        stderr_length = 0
        if isinstance(result, dict):
            stdout = result.get("stdout") or ""
            stderr = result.get("stderr") or ""
            stdout_length = len(stdout) if isinstance(stdout, str) else 0
            stderr_length = len(stderr) if isinstance(stderr, str) else 0

        _emit(
            AuditAction.CODE_EXECUTE,
            AuditOutcome.SUCCESS,
            resource_id=session_id,
            metadata={
                "session_id": session_id,
                "language": str(language),
                "code_length": code_length,
                "stdout_length": stdout_length,
                "stderr_length": stderr_length,
            },
        )
        return result

    return wrapper


def wrap_upload_file(func: Any) -> Any:
    """Wrap ``upload_file`` to emit ``code_interpreter.file.upload``."""

    @functools.wraps(func)
    async def wrapper(self: Any, session_id: str, filename: str, content: bytes) -> Any:
        size = len(content) if content is not None else 0
        try:
            result = await func(self, session_id, filename, content)
        except Exception as exc:
            _emit(
                AuditAction.CODE_FILE_UPLOAD,
                AuditOutcome.ERROR,
                resource_id=f"{session_id}/{filename}",
                metadata={
                    "session_id": session_id,
                    "filename": filename,
                    "size_bytes": size,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        _emit(
            AuditAction.CODE_FILE_UPLOAD,
            AuditOutcome.SUCCESS,
            resource_id=f"{session_id}/{filename}",
            metadata={"session_id": session_id, "filename": filename, "size_bytes": size},
        )
        return result

    return wrapper


def wrap_download_file(func: Any) -> Any:
    """Wrap ``download_file`` to emit ``code_interpreter.file.download``."""

    @functools.wraps(func)
    async def wrapper(self: Any, session_id: str, filename: str) -> Any:
        try:
            result = await func(self, session_id, filename)
        except Exception as exc:
            _emit(
                AuditAction.CODE_FILE_DOWNLOAD,
                AuditOutcome.ERROR,
                resource_id=f"{session_id}/{filename}",
                metadata={
                    "session_id": session_id,
                    "filename": filename,
                    "error_type": type(exc).__name__,
                },
            )
            raise
        size = len(result) if isinstance(result, bytes | bytearray) else 0
        _emit(
            AuditAction.CODE_FILE_DOWNLOAD,
            AuditOutcome.SUCCESS,
            resource_id=f"{session_id}/{filename}",
            metadata={"session_id": session_id, "filename": filename, "size_bytes": size},
        )
        return result

    return wrapper
