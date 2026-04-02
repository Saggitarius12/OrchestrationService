from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.orchestration.models import ExecutionStatus


class TaskBase(BaseModel):
    name: str
    instruction: str
    agent_id: Optional[UUID] = None
    input_data: Optional[dict[str, Any]] = None


class TaskCreate(TaskBase):
    # The client sends a list of UUIDs that this task depends on
    depends_on: List[UUID] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    instruction: Optional[str] = None
    agent_id: Optional[UUID] = None
    input_data: Optional[dict[str, Any]] = None


class TaskStatusUpdate(BaseModel):
    status: ExecutionStatus
    output_data: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None


class TaskResponse(TaskBase):
    id: UUID
    workflow_id: UUID
    status: ExecutionStatus
    
    depends_on: List[UUID] = Field(default_factory=list)
    
    output_data: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def extract_dependencies(cls, data: Any) -> Any:
        """Extracts UUIDs from the SQLAlchemy 'upstream_tasks' relationship."""
        if hasattr(data, "upstream_tasks") and data.upstream_tasks is not None:
            # We map the ORM objects to their UUIDs for the JSON response
            setattr(data, "depends_on",[task.id for task in data.upstream_tasks])
        return data


class TaskListResponse(BaseModel):
    items: List[TaskResponse]
    total: int