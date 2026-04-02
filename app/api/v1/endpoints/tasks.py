"""
Task REST endpoints.
"""
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.exceptions import (
    DependencyCycleError,
    InvalidTransitionError,
    NotFoundError,
    WorkflowAlreadyTerminalError,
)
from app.events.publisher import publish_event
from app.models.orchestration.models import ExecutionStatus
from app.schemas.task import (
    TaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskStatusUpdate,
    TaskUpdate,
)
from app.services.task_service import TaskService

router = APIRouter(
    prefix="/workflows/{workflow_id}/tasks",
    tags=["Tasks"],
)

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


def _svc(session: DbSession) -> TaskService:
    return TaskService(session)


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    workflow_id: UUID, body: TaskCreate, session: DbSession
) -> TaskResponse:
    try:
        task = await _svc(session).create(workflow_id, body)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except WorkflowAlreadyTerminalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return TaskResponse.model_validate(task)


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    workflow_id: UUID,
    session: DbSession,
    task_status: Optional[ExecutionStatus] = Query(None, alias="status"),
) -> TaskListResponse:
    try:
        tasks = await _svc(session).list_for_workflow(workflow_id, status=task_status)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return TaskListResponse(
        items=[TaskResponse.model_validate(t) for t in tasks],
        total=len(tasks),
    )


@router.get("/ready", response_model=TaskListResponse)
async def get_ready_tasks(workflow_id: UUID, session: DbSession) -> TaskListResponse:
    """Return tasks whose dependency tasks are all COMPLETED."""
    try:
        tasks = await _svc(session).get_ready_tasks(workflow_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return TaskListResponse(
        items=[TaskResponse.model_validate(t) for t in tasks],
        total=len(tasks),
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(workflow_id: UUID, task_id: UUID, session: DbSession) -> TaskResponse:
    try:
        task = await _svc(session).get(task_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if task.workflow_id != workflow_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found in workflow.")
    return TaskResponse.model_validate(task)


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    workflow_id: UUID, task_id: UUID, body: TaskUpdate, session: DbSession
) -> TaskResponse:
    try:
        task = await _svc(session).update(task_id, body)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except WorkflowAlreadyTerminalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return TaskResponse.model_validate(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(workflow_id: UUID, task_id: UUID, session: DbSession) -> None:
    try:
        await _svc(session).delete(task_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


# ── Status / result ───────────────────────────────────────────────────────────

@router.patch("/{task_id}/status", response_model=TaskResponse)
async def update_task_status(
    workflow_id: UUID,
    task_id: UUID,
    body: TaskStatusUpdate,
    session: DbSession,
) -> TaskResponse:
    try:
        task = await _svc(session).update_status(task_id, body)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return TaskResponse.model_validate(task)


class AgentTaskResult(TaskStatusUpdate):
    """Payload sent by the Agent Runtime Service as a callback."""
    success: bool = True


@router.post("/{task_id}/result", status_code=status.HTTP_202_ACCEPTED)
async def agent_task_result_callback(
    workflow_id: UUID,
    task_id: UUID,
    body: AgentTaskResult,
) -> dict:
    """
    Webhook called by the Agent Runtime Service when a task finishes.
    Emits a TASK_FINISHED event to Redis and returns 202 immediately.
    The Dispatcher worker will pick this up, update DB state, and advance the DAG.
    """
    # Simply push the event to Redis. Do NOT use FastAPI BackgroundTasks.
    # The dedicated dispatcher process will handle DB locking and state updates.
    await publish_event(
        "TASK_FINISHED", # Make sure this constant exists in events/publisher.py
        workflow_id=workflow_id,
        payload={
            "task_id": str(task_id),
            "success": body.success,
            "output_data": body.output_data,
            "error_message": body.error_message,
        }
    )
    
    return {"accepted": True, "task_id": str(task_id)}


# ── DAG validation ────────────────────────────────────────────────────────────

@router.post("/validate-dag", status_code=status.HTTP_200_OK)
async def validate_dag(workflow_id: UUID, session: DbSession) -> dict:
    """Validate that the task dependency graph has no cycles."""
    try:
        await _svc(session).validate_dag(workflow_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except DependencyCycleError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    return {"valid": True}