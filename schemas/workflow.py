from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from models.orchestration.models import ExecutionStatus


class WorkflowBase(BaseModel):
    goal: str = Field(..., description="The high-level goal the orchestration engine needs to achieve.")
    user_id: Optional[str] = Field(None, description="Optional ID of the user who owns this workflow.")


class WorkflowCreate(WorkflowBase):
    pass


class WorkflowUpdate(BaseModel):
    status: Optional[ExecutionStatus] = None
    result_data: Optional[dict[str, Any]] = None


class WorkflowResponse(WorkflowBase):
    id: UUID
    status: ExecutionStatus
    result_data: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    
    # We optionally include tasks if they were loaded in the repository
    tasks: Optional[List[Any]] = Field(default=None, description="List of TaskResponses")

    model_config = ConfigDict(from_attributes=True)


class WorkflowListResponse(BaseModel):
    items: List[WorkflowResponse]
    total: int
    page: int
    page_size: int