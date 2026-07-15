# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Dependencies first (better layer caching).
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

# Application code.
COPY . .

EXPOSE 8000
CMD ["./docker/entrypoint.sh"]
