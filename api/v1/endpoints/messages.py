"""
Message REST endpoints.
"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db_session
from core.exceptions import NotFoundError
from schemas.message import MessageCreate, MessageListResponse, MessageResponse
from services.message_service import MessageService

router = APIRouter(tags=["Messages"])

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def _svc(session: DbSession) -> MessageService:
    return MessageService(session)


@router.post(
    "/workflows/{workflow_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_message(
    workflow_id: UUID, body: MessageCreate, session: DbSession
) -> MessageResponse:
    try:
        msg = await _svc(session).create(workflow_id, body)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        # Handled the case where task_id does not belong to workflow_id
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        
    return MessageResponse.model_validate(msg)


@router.get(
    "/workflows/{workflow_id}/messages",
    response_model=MessageListResponse,
)
async def list_workflow_messages(
    workflow_id: UUID,
    session: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> MessageListResponse:
    offset = (page - 1) * page_size
    try:
        messages = await _svc(session).list_for_workflow(
            workflow_id, offset=offset, limit=page_size
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        
    return MessageListResponse(
        items=[MessageResponse.model_validate(m) for m in messages],
        total=len(messages),
    )


@router.get(
    "/workflows/{workflow_id}/tasks/{task_id}/messages",
    response_model=MessageListResponse,
)
async def list_task_messages(
    workflow_id: UUID, 
    task_id: UUID, 
    session: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> MessageListResponse:
    # Notice we pass workflow_id in the URL path for RESTful design, 
    # but the service only needs task_id to fetch messages.
    offset = (page - 1) * page_size
    try:
        messages = await _svc(session).list_for_task(task_id, offset=offset, limit=page_size)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        
    return MessageListResponse(
        items=[MessageResponse.model_validate(m) for m in messages],
        total=len(messages),
    )