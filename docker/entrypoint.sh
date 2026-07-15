#!/usr/bin/env bash
set -euo pipefail

# Apply migrations (safe no-op if already at head). Skipped when DATABASE_AUTO_CREATE
# is used for local sqlite quick-starts.
if [[ "${RUN_MIGRATIONS:-true}" == "true" ]]; then
  echo "Running database migrations..."
  alembic upgrade head || echo "No migrations to run (or Alembic not configured yet)."
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" "$@"
