# models/orchestration/models.py
import enum
import uuid
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import Column, DateTime, Enum, ForeignKey, JSON, String, Table, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.orchestration.base import OrchestrationBase


class ExecutionStatus(str, enum.Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"      
    RUNNING = "RUNNING"
    WAITING= "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    PAUSED = "PAUSED"



task_dependencies = Table(
    "task_dependencies",
    OrchestrationBase.metadata,
    Column("upstream_task_id", Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    Column("downstream_task_id", Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
)


# ─── Workflow ─────────────────────────────────────────────────────────────────

class WorkflowModel(OrchestrationBase):
    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[str]] = mapped_column(String, index=True)
    goal: Mapped[str] = mapped_column(String)
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus), 
        default=ExecutionStatus.PENDING
    )
    result_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

    tasks: Mapped[List["TaskModel"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )
    messages: Mapped[List["MessageModel"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )
    global_context: Mapped[dict] = mapped_column(JSON, default=dict)

    def __repr__(self) -> str:
        return f"<WorkflowModel id={self.id} status={self.status}>"


# ─── Task ─────────────────────────────────────────────────────────────────────

class TaskModel(OrchestrationBase):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), 
        index=True
    )
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, index=True)

    name: Mapped[str] = mapped_column(String)
    instruction: Mapped[str] = mapped_column(String)
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus), 
        default=ExecutionStatus.PENDING
    )
    
    input_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    output_data: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON)
    error_message: Mapped[Optional[str]] = mapped_column(String)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    workflow: Mapped["WorkflowModel"] = relationship(back_populates="tasks")
    task_type:Mapped[str] = mapped_column(String,default="Agent",server_default="Agent")

    
    upstream_tasks: Mapped[List["TaskModel"]] = relationship(
        secondary=task_dependencies,
        primaryjoin=id == task_dependencies.c.downstream_task_id,
        secondaryjoin=id == task_dependencies.c.upstream_task_id,
        backref="downstream_tasks",
    )

    def __repr__(self) -> str:
        return f"<TaskModel id={self.id} name={self.name!r} status={self.status}>"


# ─── Message ──────────────────────────────────────────────────────────────────

class MessageModel(OrchestrationBase):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True)
    
    role: Mapped[str] = mapped_column(String) # user, assistant, system
    
    
    # 'thought', 'tool_call', 'tool_result', 'final_answer'
    msg_type: Mapped[str] = mapped_column(String, default="text", server_default="text")
    content: Mapped[str] = mapped_column(String)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    
    # Prevents duplicate messages if an agent retries its HTTP call
    idempotency_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    workflow: Mapped["WorkflowModel"] = relationship(back_populates="messages")

    # Unique constraint: No two messages in the same workflow can have the same idempotency key
    __table_args__ = (
        UniqueConstraint("workflow_id", "idempotency_key", name="uq_messages_workflow_idempotency"),
    )

    def __repr__(self) -> str:
        return f"<MessageModel id={self.id} role={self.role!r}>"