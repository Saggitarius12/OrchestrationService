from typing import List
from pydantic import BaseModel, Field

class TaskDefinition(BaseModel):
    id: str = Field(..., description="A unique string identifier for this task (e.g., 'task_1').")
    name: str = Field(..., description="A short, clear name for the task.")
    instruction: str = Field(..., description="Detailed instructions for the agent executing this task.")
    agent_type: str = Field(..., description="The type of agent needed (e.g., 'researcher', 'coder').")
    depends_on: List[str] = Field(
        default_factory=list, 
        description="A list of task IDs that must complete before this task can start."
    )

class WorkflowPlan(BaseModel):
    tasks: List[TaskDefinition] = Field(
        ..., 
        description="A list of tasks that form a Directed Acyclic Graph (DAG) to achieve the user's goal."
    )