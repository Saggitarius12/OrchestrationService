"""
Celery tasks for Orchestration lifecycle management.
"""
import asyncio
import logging
from uuid import UUID

from app.core.database import async_sessionmaker
from app.services.orchestration_engine import OrchestrationEngine
from app.workers.celery_app import celery_app
from app.models.orchestration.models import TaskModel, ExecutionStatus
from datetime import timedelta, timezone, datetime
from app.events.publisher import publish_event, TASK_FINISHED
from sqlalchemy import select

log = logging.getLogger(__name__)


async def async_reap_zombies():
    """Finds tasks stuck in RUNNING for more than 20 minutes."""
    timeout_threshold = datetime.now(timezone.utc)- timedelta(minutes=20)
    
    async with async_sessionmaker() as session:
        
        stmt = select(TaskModel).where(
            TaskModel.status == ExecutionStatus.RUNNING,
            TaskModel.updated_at < timeout_threshold
        )
        result = await session.execute(stmt)
        zombies = result.scalars().all()

        for task in zombies:
            log.warning(f"Reaping zombie task {task.id} in workflow {task.workflow_id}")
            
            await publish_event(
                TASK_FINISHED,
                workflow_id=task.workflow_id,
                payload={
                    "task_id": str(task.id),
                    "success": False,
                    "error_message": "System Error: Task execution timed out (Zombie Worker)."
                }
            )
        

async def _async_advance_workflow(workflow_id: UUID) -> None:
    """Async inner function to execute the OrchestrationEngine."""
    async with async_sessionmaker() as session:
        engine = OrchestrationEngine(session)
        await engine.advance_workflow(workflow_id)


async def _async_handle_task_result(
    workflow_id: UUID, task_id: UUID, success: bool, output_data: dict, error_message: str
) -> None:
    """Async inner function to handle agent callbacks."""
    async with async_sessionmaker() as session:
        engine = OrchestrationEngine(session)
        await engine.handle_task_result(
            workflow_id=workflow_id,
            task_id=task_id,
            success=success,
            output_data=output_data,
            error_message=error_message,
        )


# ─── Celery Task Wrappers ─────────────────────────────────────────────────────

@celery_app.task(name="advance_workflow_task", bind=True, max_retries=3)
def advance_workflow_task(self, workflow_id_str: str) -> None:
    """
    Triggered when a workflow is started or resumed.
    Evaluates the DAG and dispatches the first wave of tasks.
    """
    workflow_id = UUID(workflow_id_str)
    log.info(f"Celery executing advance_workflow for {workflow_id}")
    
    try:
        # Run the async database logic inside the sync Celery worker
        asyncio.run(_async_advance_workflow(workflow_id))
    except Exception as exc:
        log.error(f"Error advancing workflow {workflow_id}: {exc}", exc_info=True)
        # Exponential backoff retry in case of transient DB locks
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


@celery_app.task(name="handle_task_result_task", bind=True, max_retries=3)
def handle_task_result_task(
    self, workflow_id_str: str, task_id_str: str, success: bool, output_data: dict = None, error_message: str = None
) -> None:
    """
    Triggered when an agent finishes a task.
    Updates task state and triggers the next step in the DAG.
    """
    workflow_id = UUID(workflow_id_str)
    task_id = UUID(task_id_str)
    log.info(f"Celery executing handle_task_result for task {task_id} (Success={success})")
    
    try:
        asyncio.run(_async_handle_task_result(
            workflow_id=workflow_id,
            task_id=task_id,
            success=success,
            output_data=output_data,
            error_message=error_message
        ))
    except Exception as exc:
        log.error(f"Error handling task result for {task_id}: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)
    
@celery_app.task(name="reap_zombies_task")
def reap_zombie_task():
    """Scheduled task to clean up zombie tasks."""
    log.info("Celery executing zombie reaper")
    try:
        asyncio.run(async_reap_zombies())
    except Exception as exc:
        log.error(f"Error reaping zombies: {exc}", exc_info=True)