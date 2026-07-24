"""Run the ingestion job worker (Phase 12).

Long-lived process: polls ``ingestion_jobs`` for queued work, claims one at a
time (``SELECT ... FOR UPDATE SKIP LOCKED`` — safe to run more than one of
this process concurrently), and reaps stuck ``running`` jobs periodically.
This is what turns an admin's "Ingest" click (``app/api/admin.py``, enqueues
a row) into an actual fetch->chunk->embed->store run, without blocking the
API request.

Run:
    python scripts/run_worker.py
    python scripts/run_worker.py --poll-interval 2 --reap-interval 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as `python scripts/run_worker.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.db import apply_schema, close_pool
from app.jobs import run_forever


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run the ingestion job worker.")
    parser.add_argument(
        "--poll-interval", type=float, default=5.0, help="Seconds between empty-queue polls."
    )
    parser.add_argument(
        "--reap-interval", type=int, default=60, help="Seconds between stuck-job reaper runs."
    )
    args = parser.parse_args()

    try:
        apply_schema()
        print("Ingestion worker started. Waiting for queued jobs (Ctrl+C to stop)...")
        run_forever(poll_interval=args.poll_interval, reap_interval=args.reap_interval)
        return 0
    except KeyboardInterrupt:
        print("\nWorker stopped.")
        return 0
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
