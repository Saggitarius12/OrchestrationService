"""Repository for MessageModel."""
from typing import Sequence
from uuid import UUID

from sqlalchemy import select

from app.models.orchestration.models import MessageModel
from app.repositories.base import BaseRepository


class MessageRepository(BaseRepository[MessageModel]):
    model = MessageModel

    async def get_by_workflow(
        self,
        workflow_id: UUID,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> Sequence[MessageModel]:
        stmt = (
            select(MessageModel)
            .where(MessageModel.workflow_id == workflow_id)
            .order_by(MessageModel.created_at)
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return result.all()

    async def get_by_task(
        self, 
        task_id: UUID,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> Sequence[MessageModel]:
        """Fetch paginated messages for a specific task."""
        stmt = (
            select(MessageModel)
            .where(MessageModel.task_id == task_id)
            .order_by(MessageModel.created_at)
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.scalars(stmt)
        return result.all()