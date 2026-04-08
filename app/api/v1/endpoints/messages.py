"""
Message REST endpoints - Orchestration Service.
Handles User and Agent generated logs.
"""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.exceptions import NotFoundError
from app.events.publisher import publish_event, MESSAGE_CREATED
from app.schemas.message import MessageCreate, MessageListResponse, MessageResponse
from app.services.message_service import MessageService

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
    """
    Appends a message to the workflow history.
    Can be called by users or by Agents via the Agent Runtime Service.
    """
    try:
        msg = await _svc(session).create(workflow_id, body)
        
        # Broadcast via Redis so any connected UI (WebSockets) can see the thought/log
        await publish_event(
            MESSAGE_CREATED,
            workflow_id=workflow_id,
            payload={
                "message_id": str(msg.id),
                "role": msg.role,
                "content": msg.content,
                "task_id": str(msg.task_id) if msg.task_id else None
            }
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
        
    return MessageResponse.model_validate(msg)


@router.get(
    "/workflows/{workflow_id}/messages",
    response_model=MessageListResponse,
)
@router.get("/workflows/{workflow_id}/messages", response_model=MessageListResponse)
async def list_workflow_messages(
    workflow_id: UUID,
    session: DbSession,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> MessageListResponse:
    offset = (page - 1) * page_size
    
    
    messages, total_count = await _svc(session).list_for_workflow(
        workflow_id, offset=offset, limit=page_size
    )
    
    return MessageListResponse(
        items=[MessageResponse.model_validate(m) for m in messages],
        total=total_count, 
        page_size=page_size
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
    offset = (page - 1) * page_size
    try:
        messages = await _svc(session).list_for_task(task_id, offset=offset, limit=page_size)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        
    return MessageListResponse(
        items=[MessageResponse.model_validate(m) for m in messages],
        total=len(messages),
    )