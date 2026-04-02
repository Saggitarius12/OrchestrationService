"""
Event routing: Bridges FastAPI to Celery tasks and Redis Pub/Sub.
"""
import json
import logging
from typing import Any, Dict
from uuid import UUID

import redis.asyncio as redis

from app.core.config import settings
# REMOVED: from app.workers.tasks import advance_workflow_task, handle_task_result_task
# We will use send_task to avoid circular imports

# Import the Celery app instance to use send_task
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)

# ── Event Constants ──
WORKFLOW_CREATED = "WORKFLOW_CREATED"
WORKFLOW_STARTED = "WORKFLOW_STARTED"
WORKFLOW_PAUSED = "WORKFLOW_PAUSED"
WORKFLOW_RESUMED = "WORKFLOW_RESUMED"

TASK_STARTED = "TASK_STARTED"
TASK_COMPLETED = "TASK_COMPLETED"
TASK_FAILED = "TASK_FAILED"
TASK_FINISHED = "TASK_FINISHED"  # Agent callback trigger
AGENT_TASK_DISPATCH = "AGENT_TASK_DISPATCH"  # Tell agent to start

MESSAGE_CREATED = "MESSAGE_CREATED"


class DefaultEncoder(json.JSONEncoder):
    """Handles serialization of UUIDs for standard Redis Pub/Sub."""
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)


async def get_redis_client() -> redis.Redis:
    redis_url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(redis_url, decode_responses=True)


async def publish_event(event_type: str, workflow_id: UUID, payload: Dict[str, Any] = None) -> None:
    """
    Routes events. Engine triggers go to Celery. UI/Agent broadcasts go to Redis Pub/Sub.
    """
    payload = payload or {}
    workflow_id_str = str(workflow_id)

    log.debug(f"Publishing event {event_type} for workflow {workflow_id_str}")

    # 1. Route to Celery for internal orchestration logic
    # We use send_task(string_name) to break the circular dependency.
    if event_type in [WORKFLOW_STARTED, WORKFLOW_RESUMED]:
        celery_app.send_task(
            "workers.tasks.advance_workflow_task", 
            args=[workflow_id_str]
        )
        
    elif event_type == TASK_FINISHED:
        celery_app.send_task(
            "workers.tasks.handle_task_result_task",
            kwargs={
                "workflow_id_str": workflow_id_str,
                "task_id_str": str(payload["task_id"]),
                "success": payload.get("success", False),
                "output_data": payload.get("output_data"),
                "error_message": payload.get("error_message")
            }
        )

    # 2. Route to standard Redis Pub/Sub for WebSockets or external non-Python Agents
    try:
        r = await get_redis_client()
        message = {
            "event_type": event_type,
            "workflow_id": workflow_id_str,
            "payload": payload
        }
        await r.publish("broadcast_events", json.dumps(message, cls=DefaultEncoder))
    except Exception as e:
        log.error(f"Failed to publish {event_type} to Redis Pub/Sub: {e}")