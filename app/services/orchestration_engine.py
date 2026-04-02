"""
Core Engine that safely evaluates the DAG and dispatches Tasks.
"""
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.events.publisher import TASK_STARTED, publish_event
from app.models.orchestration.models import ExecutionStatus, TaskModel, WorkflowModel

log = logging.getLogger(__name__)

class OrchestrationEngine:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def advance_workflow(self, workflow_id: UUID) -> None:
        """
        Evaluates the DAG, handles terminal states, and queues ready tasks.
        Uses ROW LOCKING to guarantee thread-safety during concurrent agent callbacks.
        """
        # 1. Row-Level DB Lock: Prevents race conditions from parallel workers
        stmt = (
            select(WorkflowModel)
            .where(WorkflowModel.id == workflow_id)
            .with_for_update()  # <-- CRITICAL for production orchestration!
        )
        result = await self.session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if not workflow or workflow.status not in[ExecutionStatus.PENDING, ExecutionStatus.RUNNING]:
            return  # Paused, Finished, or Not Found

        # 2. Fetch the entire task graph
        task_stmt = (
            select(TaskModel)
            .where(TaskModel.workflow_id == workflow_id)
            .options(selectinload(TaskModel.upstream_tasks))
        )
        task_result = await self.session.execute(task_stmt)
        tasks = task_result.scalars().all()

        # 3. Evaluate the state of the DAG
        all_completed = True
        any_failed = False
        ready_tasks =[]

        for task in tasks:
            if task.status == ExecutionStatus.FAILED:
                any_failed = True
            elif task.status != ExecutionStatus.COMPLETED:
                all_completed = False

            # Find tasks ready to be queued
            if task.status == ExecutionStatus.PENDING:
                deps_met = all(dep.status == ExecutionStatus.COMPLETED for dep in task.upstream_tasks)
                if deps_met:
                    ready_tasks.append(task)

        # 4. Handle workflow completion / failure
        if any_failed:
            workflow.status = ExecutionStatus.FAILED
            await self.session.commit()
            log.info(f"Workflow {workflow_id} FAILED.")
            return

        if all_completed and tasks:
            workflow.status = ExecutionStatus.COMPLETED
            await self.session.commit()
            log.info(f"Workflow {workflow_id} COMPLETED successfully.")
            return

        # 5. Dispatch ready tasks
        if workflow.status == ExecutionStatus.PENDING and ready_tasks:
            workflow.status = ExecutionStatus.RUNNING

        for task in ready_tasks:
            task.status = ExecutionStatus.QUEUED
            log.info(f"Task {task.id} QUEUED for execution.")
            
            # Here we notify the Agent Runtime Service to pick up the task!
            # You could publish this to a separate "agent_tasks" queue, or just reuse the publisher.
            await publish_event("AGENT_TASK_DISPATCH", workflow.id, {"task_id": str(task.id)})

        await self.session.commit()

    async def handle_task_result(self, workflow_id: UUID, task_id: UUID, success: bool, output_data: dict = None, error_message: str = None) -> None:
        """Called by the Dispatcher when an Agent finishes a task."""
        
        # We don't lock here; we let the task repository handle the simple update.
        # But we DO lock immediately afterwards when calling `advance_workflow`
        stmt = select(TaskModel).where(TaskModel.id == task_id)
        result = await self.session.execute(stmt)
        task = result.scalar_one_or_none()

        if not task or task.status in [ExecutionStatus.COMPLETED, ExecutionStatus.FAILED]:
            return  # Idempotency safety mechanism

        task.status = ExecutionStatus.COMPLETED if success else ExecutionStatus.FAILED
        if output_data:
            task.output_data = output_data
        if error_message:
            task.error_message = error_message

        await self.session.commit()
        log.info(f"Task {task_id} result processed. Success={success}.")

        # After saving the task result, wake up the Engine to calculate the next DAG step
        await self.advance_workflow(workflow_id)