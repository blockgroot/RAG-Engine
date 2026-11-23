#!/bin/sh
# Deploy-container entrypoint: apply the idempotent schema, then serve.
#
# Safe to run on every boot/restart/deploy — schema.sql is all
# `CREATE ... IF NOT EXISTS` (see app/db/schema.sql). Running it here means a
# fresh Postgres just works on first deploy with no separate migration step to
# remember, on any platform (Render/Railway/Fly/a VPS all just run this image).
set -e

# Schema, LLM check and embedding check in one process, skipped when nothing
# changed since the last boot (a wake-up from idle): scripts/boot.py.
echo "Applying database schema and checking config..."
python scripts/boot.py

echo "Starting API on port ${PORT:-8000}..."
# Graceful shutdown is bounded: an open answer stream otherwise held uvicorn until
# the platform's SIGKILL, and the lifespan cleanup (worker stop, summary folds,
# close_pool) never ran. 10s here + ~18s of cleanup fits Render's 30s grace.
exec uvicorn app.api.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
    --timeout-graceful-shutdown "${GRACEFUL_SHUTDOWN_SECONDS:-10}"
