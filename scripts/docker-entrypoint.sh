#!/bin/sh
# Deploy-container entrypoint: apply the idempotent schema, then serve.
#
# Safe to run on every boot/restart/deploy — schema.sql is all
# `CREATE ... IF NOT EXISTS` (see app/db/schema.sql). Running it here means a
# fresh Postgres just works on first deploy with no separate migration step to
# remember, on any platform (Render/Railway/Fly/a VPS all just run this image).
set -e

echo "Applying database schema..."
python scripts/init_db.py

echo "Starting API on port ${PORT:-8000}..."
exec uvicorn app.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
