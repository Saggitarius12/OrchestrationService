FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ── Dependencies ──────────────────────────────────────────────────────────────
FROM base AS deps
COPY pyproject.toml .
RUN pip install --upgrade pip && pip install ".[dev]"

# ── Runtime ───────────────────────────────────────────────────────────────────
FROM deps AS runtime
COPY . .

# Expose the API port
EXPOSE 8000

# Run migrations then start the server
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]

EXPOSE 8000
