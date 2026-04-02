"""
Workflow REST endpoints.
"""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.exceptions import (
    ConflictError,
    InvalidTransitionError,
    NotFoundError,
    WorkflowAlreadyTerminalError,
)
from app.models.orchestration.models import ExecutionStatus
from app.schemas.workflow import (
    WorkflowCreate,
    WorkflowListResponse,
    WorkflowResponse,
    WorkflowUpdate,
)
from app.services.workflow_service import WorkflowService

router = APIRouter(prefix="/workflows", tags=["Workflows"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def _svc(session: DbSession) -> WorkflowService:
    return WorkflowService(session)


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def create_workflow(body: WorkflowCreate, session: DbSession) -> WorkflowResponse:
    wf = await _svc(session).create(body)
    return WorkflowResponse.model_validate(wf)


@router.get("", response_model=WorkflowListResponse)
async def list_workflows(
    session: DbSession,
    user_id: Optional[str] = Query(None),
    wf_status: Optional[ExecutionStatus] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> WorkflowListResponse:
    offset = (page - 1) * page_size
    items, total = await _svc(session).list(
        user_id=user_id,
        status=wf_status,
        offset=offset,
        limit=page_size,
    )
    return WorkflowListResponse(
        items=[WorkflowResponse.model_validate(w) for w in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(workflow_id: UUID, session: DbSession) -> WorkflowResponse:
    try:
        # get_with_tasks loads the tasks so they are included in the response
        wf = await _svc(session).get_with_tasks(workflow_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return WorkflowResponse.model_validate(wf)


@router.patch("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: UUID, body: WorkflowUpdate, session: DbSession
) -> WorkflowResponse:
    try:
        wf = await _svc(session).update(workflow_id, body)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except WorkflowAlreadyTerminalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return WorkflowResponse.model_validate(wf)


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(workflow_id: UUID, session: DbSession) -> None:
    try:
        await _svc(session).delete(workflow_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


# ── Lifecycle transitions ─────────────────────────────────────────────────────

@router.post("/{workflow_id}/start", response_model=WorkflowResponse)
async def start_workflow(workflow_id: UUID, session: DbSession) -> WorkflowResponse:
    """Publishes a start event to dispatch the first wave of tasks."""
    try:
        wf = await _svc(session).start(workflow_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Workflow must be in PENDING state to start. Current: {exc}")
    except ConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return WorkflowResponse.model_validate(wf)


@router.post("/{workflow_id}/pause", response_model=WorkflowResponse)
async def pause_workflow(workflow_id: UUID, session: DbSession) -> WorkflowResponse:
    try:
        wf = await _svc(session).pause(workflow_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return WorkflowResponse.model_validate(wf)


@router.post("/{workflow_id}/resume", response_model=WorkflowResponse)
async def resume_workflow(workflow_id: UUID, session: DbSession) -> WorkflowResponse:
    try:
        wf = await _svc(session).resume(workflow_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return WorkflowResponse.model_validate(wf)