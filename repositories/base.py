"""
Generic async repository base — provides typed CRUD helpers via SQLAlchemy.
"""
from typing import Any, Generic, Sequence, TypeVar
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.orchestration.base import OrchestrationBase

ModelT = TypeVar("ModelT", bound=OrchestrationBase)


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get(self, resource_id: UUID) -> ModelT | None:
        return await self._session.get(self.model, resource_id)

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        **filters: Any,
    ) -> tuple[Sequence[ModelT], int]:
        q = select(self.model)
        for attr, value in filters.items():
            if value is not None:
                q = q.where(getattr(self.model, attr) == value)

        count_q = select(func.count()).select_from(q.subquery())
        total: int = (await self._session.scalar(count_q)) or 0

        q = q.offset(offset).limit(limit).order_by(self.model.created_at.desc())  # type: ignore[attr-defined]
        result = await self._session.scalars(q)
        return result.all(), total

    # ── Write ─────────────────────────────────────────────────────────────────

    async def create(self, instance: ModelT) -> ModelT:
        self._session.add(instance)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def update(self, instance: ModelT, **updates: Any) -> ModelT:
        for attr, value in updates.items():
            setattr(instance, attr, value)
        await self._session.flush()
        await self._session.refresh(instance)
        return instance

    async def delete(self, instance: ModelT) -> None:
        await self._session.delete(instance)
        await self._session.flush()
