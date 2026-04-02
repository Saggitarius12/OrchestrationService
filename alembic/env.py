"""
Alembic env.py — synchronous migration environment.
"""
import asyncio  # No longer needed for run, but kept for imports if necessary
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
# CHANGE 1: Import standard engine_from_config, not async
from sqlalchemy import engine_from_config 
import os
from dotenv import load_dotenv
import sys
from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import models so Alembic can detect metadata
from app.models.orchestration.base import OrchestrationBase
import app.models.orchestration.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject SYNC_DATABASE_URL from environment
db_url = os.getenv("SYNC_DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

target_metadata = OrchestrationBase.metadata

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # include_schemas=True,
        # version_table_schema="orchestration",
    )
    with context.begin_transaction():
        context.run_migrations()

# CHANGE 2: Make this function synchronous and use standard engine
def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # include_schemas=True,
            # version_table_schema="orchestration",
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()