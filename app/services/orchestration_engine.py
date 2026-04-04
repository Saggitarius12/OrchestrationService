"""
Core Engine that safely evaluates the DAG and dispatches Tasks.
Implements n8n-style Global Context sharing across the workflow.
"""
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.events.publisher import AGENT_TASK_DISPATCH, publish_event
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
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        workflow = result.scalar_one_or_none()

        if not workflow or workflow.status not in [ExecutionStatus.PENDING, ExecutionStatus.RUNNING]:
            return

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
        ready_tasks = []

        for task in tasks:
            if task.status == ExecutionStatus.FAILED:
                any_failed = True
            elif task.status != ExecutionStatus.COMPLETED:
                all_completed = False

            # Find tasks ready to be queued (PENDING and all dependencies are COMPLETED)
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
            
            
            dispatch_payload = {
                "task_id": str(task.id),
                "agent_id": str(task.agent_id),
                "instruction": task.instruction,
                "input_data": task.input_data or {},                
                "global_context": workflow.global_context or {}
            }

            log.info(f"Dispatching Task {task.id} ({task.name}) to Agent Service.")
            
            await publish_event(
                AGENT_TASK_DISPATCH, 
                workflow_id=workflow.id, 
                payload=dispatch_payload
            )

        await self.session.commit()

    async def handle_task_result(
        self, 
        workflow_id: UUID, 
        task_id: UUID, 
        success: bool, 
        output_data: dict = None, 
        error_message: str = None
    ) -> None:        
        """
        Called when an Agent finishes a task. 
        Updates task status and merges output into the global context.
        """

        # 1. Fetch the Task
        stmt_task = select(TaskModel).where(TaskModel.id == task_id)
        result_task = await self.session.execute(stmt_task)
        task = result_task.scalar_one_or_none()

        if not task or task.status in [ExecutionStatus.COMPLETED, ExecutionStatus.FAILED]:
            return  # Idempotency safety mechanism

        # 2. Fetch the Workflow with a LOCK
        
        stmt_wf = select(WorkflowModel).where(WorkflowModel.id == workflow_id).with_for_update()
        result_wf = await self.session.execute(stmt_wf)
        workflow = result_wf.scalar_one_or_none()

        if not workflow:
            log.error(f"Workflow {workflow_id} not found while processing task {task_id}")
            return

        # 3. Update Task Status & Data
        task.status = ExecutionStatus.COMPLETED if success else ExecutionStatus.FAILED
        if output_data:
            task.output_data = output_data
        if error_message:
            task.error_message = error_message

        # 4. Update Global Context (n8n logic)
        # Merges this task's result into the shared workflow memory
        if success and output_data:
            # We create a shallow copy to ensure SQLAlchemy detects the change to the JSONB column
            current_context = dict(workflow.global_context) if workflow.global_context else {}
            
            # Use task.name as the key so other nodes can reference it 
            # e.g. {{ $node["TaskName"].output }}
            current_context[task.name] = output_data
            
            workflow.global_context = current_context

        # 5. Commit state changes before advancing
        await self.session.commit()
        log.info(f"Task {task_id} result processed. Context updated for {workflow_id}.")

        # 6. Evaluate the next step in the DAG
        await self.advance_workflow(workflow_id)