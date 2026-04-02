"""
FastAPI application — entrypoint.

Responsibilities:
  - Lifespan: wire up DB pool, Redis, HTTP client, background worker
  - CORS middleware
  - Request-ID / correlation-ID middleware
  - Global exception handlers (domain → HTTP)
  - Health + readiness probes
  - Mount API v1 router
"""
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import router as api_v1_router
from app.core.config import settings
from app.core.database import close_db, get_engine
from app.core.exceptions import (
    AgentRuntimeError,
    ConflictError,
    DependencyCycleError,
    InvalidTransitionError,
    NotFoundError,
    OrchestrationError,
    WorkflowAlreadyTerminalError,
    WorkflowNotPausedError,
)
from app.core.logging import configure_logging, get_logger
from app.events.publisher import close_redis, get_redis
from app.services.orchestration_engine import close_http_client, get_http_client
from app.workers.task_executor import task_executor

configure_logging()
log = get_logger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    log.info("service_starting", service=settings.SERVICE_NAME, env=settings.ENVIRONMENT)

    # Warm up connections
    get_engine()
    get_redis()
    get_http_client()

    # Start background task executor
    await task_executor.start()

    log.info("service_ready")
    yield

    # Graceful shutdown
    await task_executor.stop()
    await close_http_client()
    await close_redis()
    await close_db()
    log.info("service_stopped")


# ── App factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Orchestration Service",
        description="Multi-agent workflow orchestration API",
        version=settings.SERVICE_VERSION,
        docs_url="/docs" if not settings.ENVIRONMENT == "production" else None,
        redoc_url="/redoc" if not settings.ENVIRONMENT == "production" else None,
        lifespan=lifespan,
    )

    # ── Middleware ────────────────────────────────────────────────────────────

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            correlation_id=correlation_id,
            path=request.url.path,
            method=request.method,
        )
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response

    # ── Exception handlers ────────────────────────────────────────────────────

    @app.exception_handler(NotFoundError)
    async def not_found_handler(request: Request, exc: NotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc), "resource": exc.resource},
        )

    @app.exception_handler(ConflictError)
    async def conflict_handler(request: Request, exc: ConflictError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(InvalidTransitionError)
    async def invalid_transition_handler(request: Request, exc: InvalidTransitionError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(DependencyCycleError)
    async def cycle_handler(request: Request, exc: DependencyCycleError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(WorkflowAlreadyTerminalError)
    async def terminal_handler(request: Request, exc: WorkflowAlreadyTerminalError):
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(AgentRuntimeError)
    async def agent_runtime_handler(request: Request, exc: AgentRuntimeError):
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(OrchestrationError)
    async def generic_orchestration_handler(request: Request, exc: OrchestrationError):
        log.error("unhandled_orchestration_error", error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal orchestration error."},
        )

    # ── Health probes ─────────────────────────────────────────────────────────

    @app.get("/health", include_in_schema=False)
    async def health() -> dict:
        return {"status": "ok", "service": settings.SERVICE_NAME}

    @app.get("/readiness", include_in_schema=False)
    async def readiness() -> dict:
        """Check DB + Redis connectivity."""
        from sqlalchemy import text
        from app.core.database import get_session_factory

        checks: dict[str, str] = {}

        # DB check
        try:
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:
            checks["database"] = f"error: {exc}"

        # Redis check
        try:
            redis = get_redis()
            await redis.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {exc}"

        overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
        code = status.HTTP_200_OK if overall == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(
            content={"status": overall, "checks": checks},
            status_code=code,
        )

    # ── Routes ────────────────────────────────────────────────────────────────

    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

    return app


app = create_app()
