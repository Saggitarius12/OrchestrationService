"""
Workflow service — business logic layer for workflow lifecycle management.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import (
    ConflictError,
    InvalidTransitionError,
    NotFoundError,
    WorkflowAlreadyTerminalError,
)
from core.logging import get_logger
from events.publisher import (
    
    WORKFLOW_CREATED,
    WORKFLOW_PAUSED,
    WORKFLOW_RESUMED,
    publish_event,
)
from models.orchestration.models import ExecutionStatus, WorkflowModel
from repositories.workflow_repository import WorkflowRepository
from schemas.workflow import WorkflowCreate, WorkflowUpdate

log = get_logger(__name__)


_TRANSITIONS: dict[ExecutionStatus, set[ExecutionStatus]] = {
    ExecutionStatus.PENDING: {ExecutionStatus.RUNNING, ExecutionStatus.FAILED},
    ExecutionStatus.RUNNING: {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.PAUSED,
    },
    ExecutionStatus.PAUSED: {ExecutionStatus.RUNNING, ExecutionStatus.FAILED},
    ExecutionStatus.COMPLETED: set(),
    ExecutionStatus.FAILED: set(),
}

TERMINAL_STATUSES = {ExecutionStatus.COMPLETED, ExecutionStatus.FAILED}


class WorkflowService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = WorkflowRepository(session)

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def create(self, data: WorkflowCreate) -> WorkflowModel:
        workflow = WorkflowModel(
            goal=data.goal,
            user_id=data.user_id,
            status=ExecutionStatus.PENDING,
        )
        workflow = await self._repo.create(workflow)
        log.info("workflow_created", workflow_id=str(workflow.id))
        await publish_event(WORKFLOW_CREATED, workflow_id=workflow.id)
        return workflow

    async def get(self, workflow_id: UUID) -> WorkflowModel:
        wf = await self._repo.get(workflow_id)
        if wf is None:
            raise NotFoundError("Workflow", workflow_id)
        return wf

    async def get_with_tasks(self, workflow_id: UUID) -> WorkflowModel:
        wf = await self._repo.get_with_tasks(workflow_id)
        if wf is None:
            raise NotFoundError("Workflow", workflow_id)
        return wf

    async def list(
        self,
        *,
        user_id: str | None = None,
        status: ExecutionStatus | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[WorkflowModel], int]:
        items, total = await self._repo.list(
            offset=offset,
            limit=limit,
            user_id=user_id,
            status=status,
        )
        return list(items), total

    async def update(self, workflow_id: UUID, data: WorkflowUpdate) -> WorkflowModel:
        wf = await self.get(workflow_id)
        if wf.status in TERMINAL_STATUSES:
            raise WorkflowAlreadyTerminalError(
                f"Workflow {workflow_id} is already in terminal state '{wf.status}'."
            )
        updates = data.model_dump(exclude_unset=True)
        return await self._repo.update(wf, **updates)

    async def delete(self, workflow_id: UUID) -> None:
        wf = await self.get(workflow_id)
        if wf.status == ExecutionStatus.RUNNING:
            raise ConflictError(
                f"Cannot delete workflow {workflow_id} while it is RUNNING."
            )
        await self._repo.delete(wf)
        log.info("workflow_deleted", workflow_id=str(workflow_id))

    # ── Lifecycle transitions ─────────────────────────────────────────────────

    async def transition(
        self,
        workflow_id: UUID,
        to_status: ExecutionStatus,
        *,
        result_data: dict | None = None,
    ) -> WorkflowModel:
        wf = await self.get(workflow_id)
        self._assert_transition(wf.status, to_status)

        updates: dict = {"status": to_status}
        if result_data is not None:
            updates["result_data"] = result_data
        updates["updated_at"] = datetime.now(timezone.utc)

        wf = await self._repo.update(wf, **updates)
        log.info(
            "workflow_status_changed",
            workflow_id=str(workflow_id),
            from_status=wf.status,
            to_status=to_status.value,
        )
        return wf

    async def start(self, workflow_id: UUID) -> WorkflowModel:
        """Validates the workflow is ready to start and publishes the start event."""
        wf = await self.get(workflow_id)
        
        if wf.status != ExecutionStatus.PENDING:
            raise InvalidTransitionError(wf.status.value, ExecutionStatus.RUNNING.value)

        
        if not wf.tasks:
             raise ConflictError(f"Workflow {workflow_id} cannot start: No task plan generated yet. Call the planner endpoint first.")

        
        await publish_event("WORKFLOW_STARTED", workflow_id=workflow_id)
        
        return wf

    async def pause(self, workflow_id: UUID) -> WorkflowModel:
        wf = await self.transition(workflow_id, ExecutionStatus.PAUSED)
        await publish_event(WORKFLOW_PAUSED, workflow_id=workflow_id)
        return wf

    async def resume(self, workflow_id: UUID) -> WorkflowModel:
        wf = await self.transition(workflow_id, ExecutionStatus.RUNNING)
        await publish_event(WORKFLOW_RESUMED, workflow_id=workflow_id)
        return wf

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _assert_transition(
        from_status: ExecutionStatus,
        to_status: ExecutionStatus,
    ) -> None:
        allowed = _TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise InvalidTransitionError(from_status.value, to_status.value)