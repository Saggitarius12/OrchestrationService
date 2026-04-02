import logging
from typing import Annotated, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.exceptions import NotFoundError
from app.models.orchestration.models import ExecutionStatus
from app.schemas.task import TaskResponse  # Ensure your TaskResponse schema can serialize the TaskModel
from app.services.planner_service import PlannerService
from app.services.workflow_service import WorkflowService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workflows/{workflow_id}/plan",
    tags=["Planner"],
)

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


@router.post(
    "",
    response_model=List[TaskResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Generate a task plan (DAG) for a workflow",
)
async def generate_workflow_plan(
    workflow_id: UUID, 
    session: DbSession
) -> List[TaskResponse]:
    """
    Invokes the AI Planner to break down the workflow's goal into a logical 
    sequence of tasks (Directed Acyclic Graph) and saves them to the database.
    """
    # 1. Fetch the workflow to ensure it exists
    wf_service = WorkflowService(session)
    try:
        wf = await wf_service.get(workflow_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    # 2. Validate state: We only plan workflows that haven't started
    if wf.status != ExecutionStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot generate a plan for a workflow in {wf.status.value} state. Must be PENDING.",
        )

    # 3. Check if a plan already exists (optional, to prevent double-planning)
    if wf.tasks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A plan (tasks) already exists for this workflow.",
        )

    # 4. Generate the plan using the AI Planner Service
    planner = PlannerService(session)
    try:
        tasks = await planner.generate_plan_for_workflow(wf)
        
        # Return the generated tasks using your Pydantic response schema
        return [TaskResponse.model_validate(t) for t in tasks]

    except ValueError as e:
        # LLM generated an invalid plan (e.g., Cyclic dependency)
        logger.error(f"Plan validation failed for workflow {workflow_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, 
            detail=str(e)
        )
    except RuntimeError as e:
        # OpenAI API failure or internal error
        logger.error(f"LLM generation failed for workflow {workflow_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, 
            detail=str(e)
        )