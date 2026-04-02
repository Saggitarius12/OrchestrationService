# models/orchestration/models.py
import enum
import uuid
from datetime import datetime
from typing import Any, List, Optional

from sqlalchemy import Column, DateTime, Enum, ForeignKey, JSON, String, Table, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.models.orchestration.base import OrchestrationBase


class ExecutionStatus(str, enum.Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"      
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
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
        DateTime(timezone=True), onupdate=func.now()
    )

    tasks: Mapped[List["TaskModel"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )
    messages: Mapped[List["MessageModel"]] = relationship(
        back_populates="workflow", cascade="all, delete-orphan"
    )

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
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), 
        index=True
    )
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE")
    )
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid, index=True)

    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    workflow: Mapped["WorkflowModel"] = relationship(back_populates="messages")

    def __repr__(self) -> str:
        return f"<MessageModel id={self.id} role={self.role!r}>"