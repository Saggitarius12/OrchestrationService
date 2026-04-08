"""
Application configuration — all values read from environment variables.
"""
from functools import lru_cache
from typing import Optional
from pydantic import AnyHttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv


load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # ── Service identity ──────────────────────────────────────────────────────
    SERVICE_NAME: str = "orchestration-service"
    SERVICE_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    DATABASE_URL: str 
    SYNC_DATABASE_URL: str 
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_PRE_PING: bool = True
    DB_ECHO: bool = False

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_EVENTS_CHANNEL: str = "orchestration:events"

    # ── Agent Runtime Service ─────────────────────────────────────────────────
    AGENT_RUNTIME_BASE_URL: AnyHttpUrl = "http://agent-runtime-service:8001"
    AGENT_RUNTIME_TIMEOUT_SECONDS: float = 120.0

    # ── Auth / JWT ────────────────────────────────────────────────────────────
    

    # ── Worker ────────────────────────────────────────────────────────────────
    WORKER_CONCURRENCY: int = 10          # Max parallel tasks per workflow
    TASK_POLL_INTERVAL_SECONDS: float = 2.0
    TASK_MAX_RETRIES: int = 3
    TASK_RETRY_BACKOFF_SECONDS: float = 5.0

    # ── API ───────────────────────────────────────────────────────────────────
    API_V1_PREFIX: str = "/api/v1"
    CORS_ORIGINS: list[str] = ["*"]

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def validate_db_url(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the 'postgresql+asyncpg' driver")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
