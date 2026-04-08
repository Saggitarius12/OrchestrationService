"""Repository for MessageModel — handles persistence and paginated retrieval."""
from typing import Sequence
from uuid import UUID

from sqlalchemy import select, func
from app.models.orchestration.models import MessageModel
from app.repositories.base import BaseRepository


class MessageRepository(BaseRepository[MessageModel]):
    model = MessageModel

    async def get_by_idempotency_key(self, workflow_id: UUID, key: str) -> MessageModel | None:
        """Find a specific message by its unique workflow + idempotency key combo."""
        stmt = select(MessageModel).where(
            MessageModel.workflow_id == workflow_id,
            MessageModel.idempotency_key == key
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_workflow(
        self,
        workflow_id: UUID,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[MessageModel], int]:
        """Fetch paginated messages for a workflow with total count."""
        # 1. Query the total count for the entire workflow
        count_stmt = select(func.count()).select_from(MessageModel).where(MessageModel.workflow_id == workflow_id)
        total_count = await self._session.scalar(count_stmt) or 0

        # 2. Query the actual paginated data slice
        stmt = (
            select(MessageModel)
            .where(MessageModel.workflow_id == workflow_id)
            .order_by(MessageModel.created_at.asc()) # Standardized ascending order
            .offset(offset)
            .limit(limit)
        )
        
        
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total_count

    async def get_by_task(
        self, 
        task_id: UUID,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[MessageModel], int]:
        """Fetch paginated messages for a specific task with total count."""
        # 1. Total count for the specific task
        count_stmt = select(func.count()).select_from(MessageModel).where(MessageModel.task_id == task_id)
        total_count = await self._session.scalar(count_stmt) or 0

        # 2. Paginated data
        stmt = (
            select(MessageModel)
            .where(MessageModel.task_id == task_id)
            .order_by(MessageModel.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total_count