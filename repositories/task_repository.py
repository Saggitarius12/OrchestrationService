"""Repository for TaskModel — adds task-specific queries."""
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from models.orchestration.models import ExecutionStatus, TaskModel
from repositories.base import BaseRepository


class TaskRepository(BaseRepository[TaskModel]):
    model = TaskModel

    async def get_by_workflow(
        self,
        workflow_id: UUID,
        *,
        status: ExecutionStatus | None = None,
    ) -> Sequence[TaskModel]:
        stmt = (
            select(TaskModel)
            .where(TaskModel.workflow_id == workflow_id)
            .options(selectinload(TaskModel.upstream_tasks))
        )
        if status is not None:
            stmt = stmt.where(TaskModel.status == status)
        stmt = stmt.order_by(TaskModel.created_at)
        result = await self._session.scalars(stmt)
        return result.all()

    async def get_pending_ready(self, workflow_id: UUID) -> Sequence[TaskModel]:
        """
        Return PENDING tasks whose every dependency is COMPLETED.
        Dependency resolution is done in Python after fetching all tasks
        for the workflow (small N, avoids complex DB query).
        """
        # get_by_workflow now eagerly loads upstream_tasks via selectinload
        all_tasks = await self.get_by_workflow(workflow_id)
        
        ready: list[TaskModel] =[]
        for task in all_tasks:
            if task.status != ExecutionStatus.PENDING:
                continue
            
            # Check if all upstream dependencies (relational objects) are COMPLETED
            if all(dep.status == ExecutionStatus.COMPLETED for dep in task.upstream_tasks):
                ready.append(task)
                
        return ready

    async def get_by_agent(self, agent_id: UUID) -> Sequence[TaskModel]:
        stmt = (
            select(TaskModel)
            .where(TaskModel.agent_id == agent_id)
            .options(selectinload(TaskModel.upstream_tasks))
        )
        result = await self._session.scalars(stmt)
        return result.all()

    async def count_by_status(
        self, workflow_id: UUID
    ) -> dict[ExecutionStatus, int]:
        tasks = await self.get_by_workflow(workflow_id)
        counts: dict[ExecutionStatus, int] = {s: 0 for s in ExecutionStatus}
        for t in tasks:
            counts[t.status] += 1
        return counts