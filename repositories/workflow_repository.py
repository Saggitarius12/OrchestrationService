"""Repository for WorkflowModel — adds workflow-specific queries."""
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from models.orchestration.models import ExecutionStatus, TaskModel, WorkflowModel
from repositories.base import BaseRepository


class WorkflowRepository(BaseRepository[WorkflowModel]):
    model = WorkflowModel

    async def get_with_tasks(self, workflow_id: UUID) -> WorkflowModel | None:
        """Eagerly load tasks and their DAG dependencies alongside the workflow."""
        stmt = (
            select(WorkflowModel)
            .where(WorkflowModel.id == workflow_id)
            .options(
                # Nested load: Load tasks, and for each task, load its upstream dependencies
                selectinload(WorkflowModel.tasks).selectinload(TaskModel.upstream_tasks)
            )
        )
        result = await self._session.scalars(stmt)
        return result.first()

    async def get_with_all(self, workflow_id: UUID) -> WorkflowModel | None:
        """Eagerly load tasks (with dependencies) AND messages."""
        stmt = (
            select(WorkflowModel)
            .where(WorkflowModel.id == workflow_id)
            .options(
                selectinload(WorkflowModel.tasks).selectinload(TaskModel.upstream_tasks),
                selectinload(WorkflowModel.messages),
            )
        )
        result = await self._session.scalars(stmt)
        return result.first()

    async def get_by_user(
        self,
        user_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        status: ExecutionStatus | None = None,
    ) -> tuple[Sequence[WorkflowModel], int]:
        """Fetch paginated workflows for a specific user."""
        return await self.list(
            offset=offset,
            limit=limit,
            user_id=user_id,
            status=status,
        )

    async def get_running(self) -> Sequence[WorkflowModel]:
        """Fetch all RUNNING workflows with their tasks and DAGs loaded."""
        stmt = (
            select(WorkflowModel)
            .where(WorkflowModel.status == ExecutionStatus.RUNNING)
            .options(
                selectinload(WorkflowModel.tasks).selectinload(TaskModel.upstream_tasks)
            )
        )
        result = await self._session.scalars(stmt)
        return result.all()