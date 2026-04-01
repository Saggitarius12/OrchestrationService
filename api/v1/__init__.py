from fastapi import APIRouter

from api.v1.endpoints import messages, tasks, workflows,planner

router = APIRouter()
router.include_router(workflows.router)
router.include_router(tasks.router)
router.include_router(messages.router)
router.include_router(planner.router)
