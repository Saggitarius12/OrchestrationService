import logging
import uuid
from graphlib import TopologicalSorter, CycleError
from typing import Dict, List

from app.services.message_service import MessageService
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings  # Assuming you have settings for API keys
from app.core.exceptions import DependencyCycleError # Assuming you have this, or raise ValueError
from app.models.orchestration.models import WorkflowModel, TaskModel, ExecutionStatus
from app.models.orchestration.models import MessageModel

from app.schemas.planner import WorkflowPlan, TaskDefinition 

logger = logging.getLogger(__name__)


# ─── The Planner Service ──────────────────────────────────────────────────────

class PlannerService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.msg_svc = MessageService(session)
        
        self.llm_client = AsyncOpenAI(api_key=getattr(settings, "OPENAI_API_KEY", None))
        self.model_name = "gpt-4o"

    async def generate_plan_for_workflow(self, workflow: WorkflowModel) -> List[TaskModel]:
        """
        Takes a workflow goal, asks the LLM to create a DAG of tasks, validates the DAG,
        and saves the tasks and their dependencies to the database.
        """
        logger.info(f"Generating LLM plan for workflow {workflow.id} goal: {workflow.goal}")
        
        
        plan = await self._fetch_plan_from_llm(workflow.goal)
        
        if not plan.requires_workflow:
            logger.info(f"Bypassing DAG for workflow {workflow.id} (Chit-chat).")
            
            
            await self.msg_svc.create(
                workflow_id=workflow.id,
                role="assistant",
                content=plan.direct_response or "Hello! How can I help you?",
                sync_to_memory=True 
            )
            
            
            workflow.status = ExecutionStatus.COMPLETED
            await self.session.commit()
            return False, plan.direct_response
        
        
        self._validate_dag(plan)
        
       
        tasks = await self._persist_plan_to_db(workflow.id, plan)
        
        return True,tasks

    async def _fetch_plan_from_llm(self, goal: str) -> WorkflowPlan:
        """Calls OpenAI using Structured Outputs to guarantee the response matches our Pydantic model."""
        system_prompt = (
            "You are an expert workflow orchestration planner. "
            "Your job is to break down the user's goal into a logical sequence of tasks. "
            "You must structure the tasks as a Directed Acyclic Graph (DAG). "
            "Tasks that can be done in parallel should not depend on each other. "
            "Tasks that require the output of previous tasks MUST include those task IDs in 'depends_on'."
        )

        try:
          
            response = await self.llm_client.beta.chat.completions.parse(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Goal: {goal}"}
                ],
                response_format=WorkflowPlan,
                temperature=0.2, 
            )
            
            plan = response.choices[0].message.parsed
            if not plan or not plan.tasks:
                raise ValueError("LLM returned an empty plan.")
                
            return plan

        except Exception as e:
            logger.error(f"Failed to generate plan from LLM: {str(e)}")
            raise RuntimeError("Failed to generate workflow plan from AI.") from e

    def _validate_dag(self, plan: WorkflowPlan) -> None:
        """
        Uses graphlib.TopologicalSorter to ensure the LLM didn't create cyclic dependencies 
        (e.g. A depends on B, B depends on A) and didn't reference non-existent tasks.
        """
        task_ids = {task.id for task in plan.tasks}
        graph = {}

        for task in plan.tasks:
            # Check for hallucinated dependencies
            for dep in task.depends_on:
                if dep not in task_ids:
                    raise ValueError(f"Task '{task.id}' depends on non-existent task '{dep}'")
            
            graph[task.id] = set(task.depends_on)

        sorter = TopologicalSorter(graph)
        try:
            
            sorter.prepare()
        except CycleError as e:
            logger.error(f"LLM generated a cyclic dependency graph: {e}")
            raise ValueError(f"Invalid workflow plan: Cyclic dependency detected ({e}).")

    async def _persist_plan_to_db(self, workflow_id: uuid.UUID, plan: WorkflowPlan) -> List[TaskModel]:
        """Translates the Pydantic plan into SQLAlchemy models and saves them."""
        db_tasks: Dict[str, TaskModel] = {}
        ordered_tasks: List[TaskModel] =[]

        
        for task_def in plan.tasks:
            new_task = TaskModel(
                id=uuid.uuid4(),
                workflow_id=workflow_id,
                name=task_def.name,
                instruction=task_def.instruction,
                
                status=ExecutionStatus.PENDING
            )
            db_tasks[task_def.id] = new_task
            ordered_tasks.append(new_task)

        # Step 2: Establish the Many-to-Many relationships
        for task_def in plan.tasks:
            current_db_task = db_tasks[task_def.id]
            
            for dep_id in task_def.depends_on:
                upstream_db_task = db_tasks[dep_id]
                current_db_task.upstream_tasks.append(upstream_db_task)

        # Step 3: Save to Database
        self.session.add_all(ordered_tasks)
        await self.session.commit()
        
        logger.info(f"Successfully saved {len(ordered_tasks)} tasks for workflow {workflow_id}")
        return ordered_tasks