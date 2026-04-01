"""
Test suite for the Orchestration Service.

Uses pytest-asyncio + in-memory SQLite (via aiosqlite) for fast unit tests.
Integration tests that need real Postgres are marked with @pytest.mark.integration.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from models.orchestration.base import OrchestrationBase
from models.orchestration.models import ExecutionStatus, TaskModel, WorkflowModel
from repositories.task_repository import TaskRepository
from repositories.workflow_repository import WorkflowRepository
from schemas.task import TaskCreate, TaskStatusUpdate
from schemas.workflow import WorkflowCreate, WorkflowUpdate
from services.task_service import TaskService
from services.workflow_service import WorkflowService
from core.exceptions import (
    DependencyCycleError,
    InvalidTransitionError,
    NotFoundError,
    WorkflowAlreadyTerminalError,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="session")
async def engine():
    """In-memory SQLite engine for tests (no Postgres required)."""
    _engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    async with _engine.begin() as conn:
        await conn.run_sync(OrchestrationBase.metadata.create_all)
    yield _engine
    await _engine.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        yield s
        await s.rollback()


@pytest_asyncio.fixture
async def workflow(session: AsyncSession) -> WorkflowModel:
    wf_repo = WorkflowRepository(session)
    wf = WorkflowModel(goal="Test goal", user_id="user-1")
    return await wf_repo.create(wf)


@pytest_asyncio.fixture
async def running_workflow(session: AsyncSession, workflow: WorkflowModel) -> WorkflowModel:
    wf_repo = WorkflowRepository(session)
    return await wf_repo.update(workflow, status=ExecutionStatus.RUNNING)


# ── WorkflowService tests ─────────────────────────────────────────────────────

class TestWorkflowService:
    async def test_create_workflow(self, session: AsyncSession) -> None:
        svc = WorkflowService(session)
        with patch("services.workflow_service.publish_event", new_callable=AsyncMock):
            wf = await svc.create(WorkflowCreate(goal="Build a chatbot", user_id="u1"))
        assert wf.id is not None
        assert wf.status == ExecutionStatus.PENDING
        assert wf.goal == "Build a chatbot"

    async def test_get_existing_workflow(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        svc = WorkflowService(session)
        result = await svc.get(workflow.id)
        assert result.id == workflow.id

    async def test_get_missing_workflow_raises(self, session: AsyncSession) -> None:
        svc = WorkflowService(session)
        with pytest.raises(NotFoundError):
            await svc.get(uuid.uuid4())

    async def test_update_workflow(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        svc = WorkflowService(session)
        updated = await svc.update(workflow.id, WorkflowUpdate(goal="Updated goal"))
        assert updated.goal == "Updated goal"

    async def test_update_terminal_workflow_raises(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        wf_repo = WorkflowRepository(session)
        await wf_repo.update(workflow, status=ExecutionStatus.COMPLETED)
        svc = WorkflowService(session)
        with pytest.raises(WorkflowAlreadyTerminalError):
            await svc.update(workflow.id, WorkflowUpdate(goal="New goal"))

    async def test_valid_status_transitions(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        svc = WorkflowService(session)
        with patch("services.workflow_service.publish_event", new_callable=AsyncMock):
            running = await svc.transition(workflow.id, ExecutionStatus.RUNNING)
            assert running.status == ExecutionStatus.RUNNING

            paused = await svc.transition(workflow.id, ExecutionStatus.PAUSED)
            assert paused.status == ExecutionStatus.PAUSED

    async def test_invalid_status_transition_raises(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        svc = WorkflowService(session)
        with pytest.raises(InvalidTransitionError):
            # PENDING → COMPLETED is not allowed
            await svc.transition(workflow.id, ExecutionStatus.COMPLETED)

    async def test_list_workflows_by_user(self, session: AsyncSession) -> None:
        svc = WorkflowService(session)
        with patch("services.workflow_service.publish_event", new_callable=AsyncMock):
            await svc.create(WorkflowCreate(goal="wf1", user_id="u-list"))
            await svc.create(WorkflowCreate(goal="wf2", user_id="u-list"))
            await svc.create(WorkflowCreate(goal="wf3", user_id="other-user"))

        items, total = await svc.list(user_id="u-list")
        assert total >= 2
        assert all(w.user_id == "u-list" for w in items)


# ── TaskService tests ─────────────────────────────────────────────────────────

class TestTaskService:
    async def test_create_task(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        svc = TaskService(session)
        with patch("services.task_service.publish_event", new_callable=AsyncMock):
            task = await svc.create(
                workflow.id,
                TaskCreate(name="Search web", instruction="Search for Python docs"),
            )
        assert task.id is not None
        assert task.workflow_id == workflow.id
        assert task.status == ExecutionStatus.PENDING

    async def test_create_task_with_dependencies(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        svc = TaskService(session)
        with patch("services.task_service.publish_event", new_callable=AsyncMock):
            t1 = await svc.create(
                workflow.id, TaskCreate(name="T1", instruction="Step 1")
            )
            t2 = await svc.create(
                workflow.id,
                TaskCreate(
                    name="T2",
                    instruction="Step 2",
                    depends_on=[str(t1.id)],
                ),
            )
        assert str(t1.id) in (t2.depends_on or [])

    async def test_task_not_found_raises(self, session: AsyncSession) -> None:
        svc = TaskService(session)
        with pytest.raises(NotFoundError):
            await svc.get(uuid.uuid4())

    async def test_update_task_status_to_running(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        svc = TaskService(session)
        with patch("services.task_service.publish_event", new_callable=AsyncMock):
            task = await svc.create(
                workflow.id, TaskCreate(name="T", instruction="Do something")
            )
            updated = await svc.update_status(
                task.id, TaskStatusUpdate(status=ExecutionStatus.RUNNING)
            )
        assert updated.status == ExecutionStatus.RUNNING
        assert updated.started_at is not None

    async def test_complete_task_sets_completed_at(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        svc = TaskService(session)
        with patch("services.task_service.publish_event", new_callable=AsyncMock):
            task = await svc.create(
                workflow.id, TaskCreate(name="T", instruction="Run")
            )
            await svc.update_status(task.id, TaskStatusUpdate(status=ExecutionStatus.RUNNING))
            done = await svc.update_status(
                task.id,
                TaskStatusUpdate(
                    status=ExecutionStatus.COMPLETED,
                    output_data={"result": "done"},
                ),
            )
        assert done.status == ExecutionStatus.COMPLETED
        assert done.completed_at is not None
        assert done.output_data == {"result": "done"}

    async def test_invalid_task_transition_raises(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        svc = TaskService(session)
        with patch("services.task_service.publish_event", new_callable=AsyncMock):
            task = await svc.create(
                workflow.id, TaskCreate(name="T", instruction="X")
            )
        with pytest.raises(InvalidTransitionError):
            await svc.update_status(
                task.id, TaskStatusUpdate(status=ExecutionStatus.COMPLETED)
            )

    async def test_dag_validation_no_cycle(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        svc = TaskService(session)
        with patch("services.task_service.publish_event", new_callable=AsyncMock):
            t1 = await svc.create(workflow.id, TaskCreate(name="A", instruction="a"))
            t2 = await svc.create(
                workflow.id,
                TaskCreate(name="B", instruction="b", depends_on=[str(t1.id)]),
            )
            t3 = await svc.create(
                workflow.id,
                TaskCreate(
                    name="C", instruction="c", depends_on=[str(t1.id), str(t2.id)]
                ),
            )
        # Should not raise
        await svc.validate_dag(workflow.id)

    async def test_get_ready_tasks(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        svc = TaskService(session)
        with patch("services.task_service.publish_event", new_callable=AsyncMock):
            t1 = await svc.create(workflow.id, TaskCreate(name="A", instruction="a"))
            t2 = await svc.create(
                workflow.id,
                TaskCreate(name="B", instruction="b", depends_on=[str(t1.id)]),
            )

        # Only T1 should be ready (T2 depends on T1 which is PENDING)
        ready = await svc.get_ready_tasks(workflow.id)
        ready_ids = {str(t.id) for t in ready}
        assert str(t1.id) in ready_ids
        assert str(t2.id) not in ready_ids


# ── Repository tests ──────────────────────────────────────────────────────────

class TestWorkflowRepository:
    async def test_list_pagination(self, session: AsyncSession) -> None:
        repo = WorkflowRepository(session)
        for i in range(5):
            await repo.create(WorkflowModel(goal=f"Goal {i}", user_id="paginate-user"))

        page1, total = await repo.list(offset=0, limit=3, user_id="paginate-user")
        assert len(page1) == 3
        assert total >= 5

        page2, _ = await repo.list(offset=3, limit=3, user_id="paginate-user")
        assert len(page2) >= 2

    async def test_delete_cascades_to_tasks(
        self, session: AsyncSession, workflow: WorkflowModel
    ) -> None:
        task_repo = TaskRepository(session)
        wf_repo = WorkflowRepository(session)

        task = TaskModel(
            workflow_id=workflow.id,
            name="cascade-task",
            instruction="x",
        )
        await task_repo.create(task)

        await wf_repo.delete(workflow)
        tasks = await task_repo.get_by_workflow(workflow.id)
        assert len(tasks) == 0
