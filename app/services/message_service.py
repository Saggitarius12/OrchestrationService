"""
Message service — business logic for message logging within workflows and tasks.
"""
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.events.publisher import publish_event
from app.models.orchestration.models import MessageModel, TaskModel, WorkflowModel
from app.repositories.message_repository import MessageRepository
from app.repositories.task_repository import TaskRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.message import MessageCreate

log = get_logger(__name__)


class MessageService:
    def __init__(self, session: AsyncSession) -> None:
        self._msg_repo = MessageRepository(session)
        self._wf_repo = WorkflowRepository(session)
        self._task_repo = TaskRepository(session)

    async def create(self, workflow_id: UUID, data: MessageCreate) -> MessageModel:
        # 1. Validate Workflow exists
        wf = await self._wf_repo.get(workflow_id)
        if not wf:
            raise NotFoundError("Workflow", workflow_id)

        # 2. Validate Task exists (if provided)
        if data.task_id:
            task = await self._task_repo.get(data.task_id)
            if not task:
                raise NotFoundError("Task", data.task_id)
            if task.workflow_id != workflow_id:
                raise ValueError(f"Task {data.task_id} does not belong to Workflow {workflow_id}")

        # 3. Create Message
        msg = MessageModel(
            workflow_id=workflow_id,
            task_id=data.task_id,
            agent_id=data.agent_id,
            role=data.role,
            content=data.content,
        )
        msg = await self._msg_repo.create(msg)
        
        log.info(
            "message_created", 
            message_id=str(msg.id), 
            workflow_id=str(workflow_id), 
            task_id=str(data.task_id) if data.task_id else None
        )

        # 4. Publish Event (Crucial for real-time UI updates / WebSockets)
        await publish_event(
            "MESSAGE_CREATED", 
            workflow_id=workflow_id, 
            payload={
                "message_id": str(msg.id),
                "task_id": str(msg.task_id) if msg.task_id else None,
                "role": msg.role,
                "content": msg.content
            }
        )

        return msg

    async def list_for_workflow(
        self, workflow_id: UUID, offset: int = 0, limit: int = 50
    ) -> list[MessageModel]:
        # Ensure workflow exists before fetching
        wf = await self._wf_repo.get(workflow_id)
        if not wf:
            raise NotFoundError("Workflow", workflow_id)
            
        messages = await self._msg_repo.get_by_workflow(
            workflow_id, offset=offset, limit=limit
        )
        return list(messages)

    async def list_for_task(
        self, task_id: UUID, offset: int = 0, limit: int = 50
    ) -> list[MessageModel]:
        # Ensure task exists before fetching
        task = await self._task_repo.get(task_id)
        if not task:
            raise NotFoundError("Task", task_id)

        messages = await self._msg_repo.get_by_task(
            task_id, offset=offset, limit=limit
        )
        return list(messages)