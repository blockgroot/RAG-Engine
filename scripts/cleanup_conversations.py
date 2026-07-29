"""Delete inactive conversations past their retention window.

A conversation is inactive once its most recent turn (or, for a conversation
with no turns, its creation time) is older than ``MEMORY_CONVERSATION_RETENTION_DAYS``
(default 90). Deleting it cascades to its turns and last-retrieval row — see
``app/memory/pg_store.py::delete_stale_conversations``.

Run once (e.g. from an external cron / systemd timer / k8s CronJob):
    python scripts/cleanup_conversations.py --once

Or as its own long-lived process, sweeping on an interval (mirrors
``scripts/run_worker.py``'s pattern — no new infra, just another small script):
    python scripts/cleanup_conversations.py
    python scripts/cleanup_conversations.py --interval-hours 6 --retention-days 30
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.config.settings import MemorySettings
from app.db import apply_schema, close_pool
from app.memory import delete_stale_conversations


def run_once(retention_days: int) -> int:
    deleted = delete_stale_conversations(retention_days)
    print(f"Deleted {deleted} conversation(s) inactive for more than {retention_days} day(s).")
    return deleted


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Clean up inactive conversations.")
    parser.add_argument(
        "--retention-days",
        type=int,
        default=None,
        help="Override MEMORY_CONVERSATION_RETENTION_DAYS for this run.",
    )
    parser.add_argument(
        "--once", action="store_true", help="Run a single sweep and exit (cron-friendly)."
    )
    parser.add_argument(
        "--interval-hours",
        type=float,
        default=24.0,
        help="Hours between sweeps when not run with --once.",
    )
    args = parser.parse_args()

    retention_days = args.retention_days
    if retention_days is None:
        retention_days = MemorySettings.from_env().conversation_retention_days

    try:
        apply_schema()
        if args.once:
            run_once(retention_days)
            return 0

        print(
            f"Conversation cleanup started: sweeping every {args.interval_hours}h, "
            f"retention {retention_days}d (Ctrl+C to stop)..."
        )
        while True:
            run_once(retention_days)
            time.sleep(args.interval_hours * 3600)
    except KeyboardInterrupt:
        print("\nCleanup stopped.")
        return 0
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
