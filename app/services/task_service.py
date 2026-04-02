"""
Task service — business logic for task lifecycle within a workflow.
"""
import graphlib
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    DependencyCycleError,
    InvalidTransitionError,
    NotFoundError,
    WorkflowAlreadyTerminalError,
)
from app.core.logging import get_logger
from app.events.publisher import TASK_COMPLETED, TASK_FAILED, TASK_STARTED, publish_event
from app.models.orchestration.models import ExecutionStatus, TaskModel, WorkflowModel
from app.repositories.task_repository import TaskRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.task import TaskCreate, TaskStatusUpdate, TaskUpdate

log = get_logger(__name__)

# Updated to include QUEUED state for the dispatcher worker
_TASK_TRANSITIONS: dict[ExecutionStatus, set[ExecutionStatus]] = {
    ExecutionStatus.PENDING: {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING, ExecutionStatus.FAILED},
    ExecutionStatus.QUEUED: {ExecutionStatus.RUNNING, ExecutionStatus.FAILED, ExecutionStatus.PAUSED},
    ExecutionStatus.RUNNING: {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED, ExecutionStatus.PAUSED},
    ExecutionStatus.PAUSED: {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING, ExecutionStatus.FAILED},
    ExecutionStatus.COMPLETED: set(),
    ExecutionStatus.FAILED: set(),
}

TERMINAL_STATUSES = {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED}


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session # Needed for custom dependency queries
        self._task_repo = TaskRepository(session)
        self._wf_repo = WorkflowRepository(session)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def create(self, workflow_id: UUID, data: TaskCreate) -> TaskModel:
        wf = await self._get_workflow(workflow_id)
        if wf.status in {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED}:
            raise WorkflowAlreadyTerminalError(
                f"Cannot add tasks to terminal workflow {workflow_id}."
            )

        # 1. Base Task Creation
        task = TaskModel(
            workflow_id=workflow_id,
            agent_id=data.agent_id,
            name=data.name,
            instruction=data.instruction,
            input_data=data.input_data,
            status=ExecutionStatus.PENDING,
        )

        # 2. Resolve DAG Dependencies (Replaces the old JSON depends_on column)
        # Assuming data.depends_on is a list of UUIDs coming from the schema
        if data.depends_on:
            stmt = select(TaskModel).where(TaskModel.id.in_(data.depends_on))
            result = await self.session.execute(stmt)
            upstream_tasks = list(result.scalars().all())
            
            if len(upstream_tasks) != len(data.depends_on):
                raise NotFoundError("One or more upstream tasks provided in 'depends_on' were not found.", data.depends_on)
            
            task.upstream_tasks.extend(upstream_tasks)

        task = await self._task_repo.create(task)
        log.info("task_created", task_id=str(task.id), workflow_id=str(workflow_id))
        return task

    async def get(self, task_id: UUID) -> TaskModel:
        # Update your repo or use selectinload to ensure upstream_tasks are fetched
        stmt = select(TaskModel).where(TaskModel.id == task_id).options(selectinload(TaskModel.upstream_tasks))
        result = await self.session.execute(stmt)
        task = result.scalar_one_or_none()
        
        if task is None:
            raise NotFoundError("Task", task_id)
        return task

    async def list_for_workflow(
        self,
        workflow_id: UUID,
        *,
        status: ExecutionStatus | None = None,
    ) -> list[TaskModel]:
        await self._get_workflow(workflow_id)
        tasks = await self._task_repo.get_by_workflow(workflow_id, status=status)
        return list(tasks)

    async def update(self, task_id: UUID, data: TaskUpdate) -> TaskModel:
        task = await self.get(task_id)
        if task.status in TERMINAL_STATUSES:
            raise WorkflowAlreadyTerminalError(
                f"Task {task_id} is already in terminal state '{task.status}'."
            )
        updates = data.model_dump(exclude_unset=True)
        return await self._task_repo.update(task, **updates)

    async def delete(self, task_id: UUID) -> None:
        task = await self.get(task_id)
        if task.status in {ExecutionStatus.RUNNING, ExecutionStatus.QUEUED}:
            raise InvalidTransitionError(task.status.value, "DELETED")
        await self._task_repo.delete(task)

    # ── Status transitions ────────────────────────────────────────────────────

    async def update_status(
        self, task_id: UUID, data: TaskStatusUpdate
    ) -> TaskModel:
        task = await self.get(task_id)
        allowed = _TASK_TRANSITIONS.get(task.status, set())
        if data.status not in allowed:
            raise InvalidTransitionError(task.status.value, data.status.value)

        updates: dict = {"status": data.status}

        if data.status == ExecutionStatus.RUNNING:
            updates["started_at"] = datetime.now(timezone.utc)
        elif data.status in TERMINAL_STATUSES:
            updates["completed_at"] = datetime.now(timezone.utc)
            if data.output_data is not None:
                updates["output_data"] = data.output_data
            if data.error_message is not None:
                updates["error_message"] = data.error_message

        task = await self._task_repo.update(task, **updates)

        # Generic status events (useful for UI websockets/audit logs)
        event_map = {
            ExecutionStatus.RUNNING: TASK_STARTED,
            ExecutionStatus.COMPLETED: TASK_COMPLETED,
            ExecutionStatus.FAILED: TASK_FAILED,
        }
        if event_type := event_map.get(data.status):
            await publish_event(
                event_type,
                workflow_id=task.workflow_id,
                payload={"task_id": str(task_id)},
            )

        log.info(
            "task_status_updated",
            task_id=str(task_id),
            new_status=data.status.value,
        )
        return task

    # ── DAG validation ────────────────────────────────────────────────────────

    async def validate_dag(self, workflow_id: UUID) -> None:
        """
        Uses Python's built-in graphlib to validate the topological sort and detect dependency cycles.
        Raises DependencyCycleError if a cycle is found.
        """
        stmt = select(TaskModel).where(TaskModel.workflow_id == workflow_id).options(selectinload(TaskModel.upstream_tasks))
        result = await self.session.execute(stmt)
        tasks = result.scalars().all()

        graph = {}
        for task in tasks:
            # Map each task to a set of its dependencies
            graph[str(task.id)] = {str(dep.id) for dep in task.upstream_tasks}

        sorter = graphlib.TopologicalSorter(graph)
        try:
            sorter.prepare()
        except graphlib.CycleError as e:
            raise DependencyCycleError(
                f"Workflow {workflow_id} task graph contains a cycle: {e}"
            )

    async def get_ready_tasks(self, workflow_id: UUID) -> list[TaskModel]:
        """Return tasks whose dependencies are all satisfied."""
        # Typically handled by the orchestration engine, but useful as a helper
        return list(await self._task_repo.get_pending_ready(workflow_id))

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _get_workflow(self, workflow_id: UUID) -> WorkflowModel:
        wf = await self._wf_repo.get(workflow_id)
        if wf is None:
            raise NotFoundError("Workflow", workflow_id)
        return wf