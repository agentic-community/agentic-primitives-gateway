from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile

from agentic_primitives_gateway.audit.emit import audit_mutation
from agentic_primitives_gateway.audit.models import AuditAction, ResourceType
from agentic_primitives_gateway.models.code_interpreter import (
    ExecuteRequest,
    ExecutionHistoryEntry,
    ExecutionHistoryResponse,
    ExecutionResult,
    FileUploadResponse,
    ListSessionsResponse,
    SessionInfo,
    StartSessionRequest,
)
from agentic_primitives_gateway.models.enums import Primitive
from agentic_primitives_gateway.registry import registry
from agentic_primitives_gateway.routes._helpers import (
    code_interpreter_session_owners,
    handle_provider_errors,
    require_principal,
)

router = APIRouter(
    prefix="/api/v1/code-interpreter",
    tags=[Primitive.CODE_INTERPRETER],
    dependencies=[Depends(require_principal)],
)


@router.post("/sessions", response_model=SessionInfo, status_code=201)
async def start_session(request: StartSessionRequest) -> SessionInfo:
    principal = require_principal()
    async with audit_mutation(
        AuditAction.SESSION_CREATE,
        resource_type=ResourceType.SESSION,
        metadata={"primitive": "code_interpreter", "language": request.language},
    ) as audit:
        result = await registry.code_interpreter.start_session(
            session_id=request.session_id,
            config={"language": request.language, **request.config},
        )
        info = SessionInfo(**result)
        await code_interpreter_session_owners.set_owner(info.session_id, principal.id)
        audit.resource_id = info.session_id
    return info


@router.delete("/sessions/{session_id}")
async def stop_session(session_id: str) -> Response:
    await code_interpreter_session_owners.require_owner(session_id, require_principal())
    async with audit_mutation(
        AuditAction.SESSION_TERMINATE,
        resource_type=ResourceType.SESSION,
        resource_id=session_id,
        metadata={"primitive": "code_interpreter"},
    ):
        await registry.code_interpreter.stop_session(session_id)
        await code_interpreter_session_owners.delete(session_id)
    return Response(status_code=204)


@router.get("/sessions", response_model=ListSessionsResponse)
async def list_sessions(status: str | None = None) -> ListSessionsResponse:
    principal = require_principal()
    sessions = await registry.code_interpreter.list_sessions(status=status)
    all_infos = [SessionInfo(**s) for s in sessions]
    if principal.is_admin:
        return ListSessionsResponse(sessions=all_infos)
    owned = await code_interpreter_session_owners.owned_session_ids(principal.id)
    return ListSessionsResponse(sessions=[s for s in all_infos if s.session_id in owned])


@router.get("/sessions/{session_id}/history", response_model=ExecutionHistoryResponse)
@handle_provider_errors("Execution history not supported by this provider", not_found="Session not found")
async def get_execution_history(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=500),
) -> Any:
    await code_interpreter_session_owners.require_owner(session_id, require_principal())
    entries = await registry.code_interpreter.get_execution_history(session_id, limit=limit)
    return ExecutionHistoryResponse(entries=[ExecutionHistoryEntry(**e) for e in entries])


@router.get("/sessions/{session_id}", response_model=SessionInfo)
@handle_provider_errors("get_session not supported by this provider", not_found="Session not found")
async def get_session(session_id: str) -> Any:
    await code_interpreter_session_owners.require_owner(session_id, require_principal())
    result = await registry.code_interpreter.get_session(session_id)
    return SessionInfo(**result)


@router.post("/sessions/{session_id}/execute", response_model=ExecutionResult)
async def execute_code(session_id: str, request: ExecuteRequest) -> ExecutionResult:
    await code_interpreter_session_owners.require_owner(session_id, require_principal())
    # Do NOT log the code body — may contain credentials the agent
    # inlined.  Length + language are enough for forensics.
    async with audit_mutation(
        AuditAction.CODE_EXECUTE,
        resource_type=ResourceType.CODE_EXECUTION,
        resource_id=session_id,
        metadata={"language": request.language, "code_length": len(request.code)},
    ):
        try:
            result = await registry.code_interpreter.execute(
                session_id=session_id,
                code=request.code,
                language=request.language,
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    return ExecutionResult(**result)


@router.post("/sessions/{session_id}/files", response_model=FileUploadResponse)
async def upload_file(session_id: str, file: UploadFile) -> FileUploadResponse:
    await code_interpreter_session_owners.require_owner(session_id, require_principal())
    content = await file.read()
    async with audit_mutation(
        AuditAction.CODE_FILE_UPLOAD,
        resource_type=ResourceType.FILE,
        resource_id=f"{session_id}/{file.filename or 'upload'}",
        metadata={"size_bytes": len(content)},
    ):
        try:
            result = await registry.code_interpreter.upload_file(
                session_id=session_id,
                filename=file.filename or "upload",
                content=content,
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
    return FileUploadResponse(**result)


@router.get("/sessions/{session_id}/files/{filename}")
async def download_file(session_id: str, filename: str) -> Response:
    await code_interpreter_session_owners.require_owner(session_id, require_principal())
    async with audit_mutation(
        AuditAction.CODE_FILE_DOWNLOAD,
        resource_type=ResourceType.FILE,
        resource_id=f"{session_id}/{filename}",
    ) as audit:
        try:
            content = await registry.code_interpreter.download_file(session_id=session_id, filename=filename)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        audit.metadata["size_bytes"] = len(content)
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
