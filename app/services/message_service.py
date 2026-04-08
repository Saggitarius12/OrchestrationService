"""
Message service — business logic for message logging within workflows and tasks.
Refactored for Dual-Store Pattern: OS DB (UI) + Memory Service Sync (AI).
"""
from uuid import UUID
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.events.publisher import publish_event, MESSAGE_CREATED
from app.models.orchestration.models import MessageModel
from app.repositories.message_repository import MessageRepository
from app.repositories.task_repository import TaskRepository
from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.message import MessageCreate
from app.workers.celery_app import celery_app 
from sqlalchemy.exc import IntegrityError

log = get_logger(__name__)


class MessageService:
    def __init__(self, session: AsyncSession) -> None:
        self._msg_repo = MessageRepository(session)
        self._wf_repo = WorkflowRepository(session)
        self._task_repo = TaskRepository(session)

    async def create(
    self, 
    workflow_id: UUID, 
    data: MessageCreate, 
    sync_to_memory: bool = True
) -> MessageModel:
    # 1. OPTIMIZATION: Check for existing message if idempotency_key is provided
        if data.idempotency_key:
            existing = await self._msg_repo.get_by_idempotency_key(workflow_id, data.idempotency_key)
            if existing:
                log.info("message_idempotency_hit", workflow_id=str(workflow_id), key=data.idempotency_key)
                return existing

        # 2. VALIDATION: Ensure Workflow and Task exist
        wf = await self._wf_repo.get(workflow_id)
        if not wf:
            raise NotFoundError("Workflow", workflow_id)

        if data.task_id:
            task = await self._task_repo.get(data.task_id)
            if not task:
                raise NotFoundError("Task", data.task_id)
            if task.workflow_id != workflow_id:
                raise ValueError(f"Task {data.task_id} does not belong to Workflow {workflow_id}")

        # 3. PERSISTENCE: Create Message with enriched production fields
        msg = MessageModel(
            workflow_id=workflow_id,
            task_id=data.task_id,
            agent_id=data.agent_id,
            role=data.role,
            content=data.content,
            msg_type=data.msg_type,         # e.g., 'text', 'thought', 'tool_result'
            metadata_json=data.metadata_json, # dynamic metadata
            idempotency_key=data.idempotency_key
        )
        
        try:
            msg = await self._msg_repo.create(msg)
        except IntegrityError:
            # SAFETY: If a race condition occurred, fetch the record created by the other thread
            await self.session.rollback()
            log.warning("message_race_condition_handled", workflow_id=str(workflow_id))
            return await self._msg_repo.get_by_idempotency_key(workflow_id, data.idempotency_key)

        log.info("message_created_locally", message_id=str(msg.id), workflow_id=str(workflow_id))

        # 4. NON-BLOCKING: Publish Event for UI (WebSockets)
        
        asyncio.create_task(
            publish_event(
                MESSAGE_CREATED, 
                workflow_id=workflow_id, 
                payload={
                    "message_id": str(msg.id),
                    "task_id": str(msg.task_id) if msg.task_id else None,
                    "role": msg.role,
                    "msg_type": msg.msg_type,
                    "content": msg.content,
                    "metadata": msg.metadata_json
                }
            )
        )

        # 5. DUAL-STORE SYNC: Background Sync to Memory Service 
        
        if sync_to_memory and data.role in ["user", "assistant"]:
            log.debug("queueing_memory_sync", message_id=str(msg.id))
            celery_app.send_task(
                "sync_message_to_memory_service",
                kwargs={
                    "workflow_id": str(workflow_id),
                    "role": data.role,
                    "content": data.content,
                    "metadata": {
                        "task_id": str(data.task_id) if data.task_id else None,
                        "agent_id": str(data.agent_id) if data.agent_id else None,
                        "msg_type": data.msg_type,
                        "os_message_id": str(msg.id)
                    }
                }
            )

        return msg

    async def list_for_workflow(
        self, workflow_id: UUID, offset: int = 0, limit: int = 50
    ) -> list[MessageModel]:
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
        task = await self._task_repo.get(task_id)
        if not task:
            raise NotFoundError("Task", task_id)

        messages = await self._msg_repo.get_by_task(
            task_id, offset=offset, limit=limit
        )
        return list(messages)