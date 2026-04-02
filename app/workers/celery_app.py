"""
Celery Application initialization and configuration.
"""
from celery import Celery

from app.core.config import settings

# Initialize Celery
# We use Redis as both the broker (message queue) and backend (result store)
celery_app = Celery(
    "orchestration_worker",
    broker=getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
    backend=getattr(settings, "REDIS_URL", "redis://localhost:6379/0"),
    include=["workers.tasks"]  # Instructs Celery to look for tasks here
)

# Celery Configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Acknowledge the task only AFTER it finishes successfully
    task_acks_late=True,
    # Ensure one worker doesn't hoard all tasks if others are free
    worker_prefetch_multiplier=1,
)