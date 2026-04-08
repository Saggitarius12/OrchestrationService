from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessageBase(BaseModel):
    task_id: Optional[UUID] = Field(None, description="Optional task this message belongs to")
    agent_id: Optional[UUID] = Field(None, description="Optional agent that generated this message")
    role: str = Field(..., description="e.g., 'user', 'system', 'agent', 'planner'")
    content: str = Field(..., description="The actual message or log content")


class MessageCreate(BaseModel):
    role: str
    content: str
    task_id: Optional[UUID] = None
    msg_type: str = "text"
    metadata_json: Optional[dict] = {}
    idempotency_key: Optional[str] = None # Highly recommended for Agents to generate this


class MessageResponse(MessageBase):
    id: UUID
    workflow_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MessageListResponse(BaseModel):
    items: List[MessageResponse]
    total: int