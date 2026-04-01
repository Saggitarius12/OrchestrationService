# Orchestration Service

Production-grade multi-agent orchestration service built with FastAPI, SQLAlchemy (async), and PostgreSQL.

## Architecture

```
orchestration_service/
├── api/v1/endpoints/       # FastAPI routers
│   ├── workflows.py
│   ├── tasks.py
│   └── messages.py
├── core/                   # Config, DB, logging, exceptions
│   ├── config.py
│   ├── database.py
│   ├── exceptions.py
│   └── logging.py
├── events/                 # Event publishing (Redis Pub/Sub)
│   └── publisher.py
├── models/orchestration/   # SQLAlchemy ORM models
│   ├── base.py
│   └── models.py
├── repositories/           # Data access layer
│   ├── base.py
│   ├── workflow_repository.py
│   ├── task_repository.py
│   └── message_repository.py
├── schemas/                # Pydantic request/response schemas
│   ├── workflow.py
│   ├── task.py
│   └── message.py
├── services/               # Business logic
│   ├── workflow_service.py
│   ├── task_service.py
│   └── orchestration_engine.py
├── workers/                # Background task runners
│   └── task_executor.py
├── main.py
├── pyproject.toml
└── alembic/
```

## Key Features
- Async SQLAlchemy with PostgreSQL
- DAG-based task dependency resolution
- Redis-backed event publishing for inter-service communication
- Background task execution via asyncio workers
- Full CRUD for Workflows, Tasks, Messages
- Structured logging with correlation IDs
- Health + readiness probes
- Alembic migrations
