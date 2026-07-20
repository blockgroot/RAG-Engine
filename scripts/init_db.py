"""Initialize the database: create the pgvector extension and tables.

Reads DATABASE_URL from the environment (or `.env`) and applies the idempotent
schema. Safe to run repeatedly.

Run:
    python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/init_db.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.config.settings import DatabaseSettings
from app.core.exceptions import ProviderError
from app.db import apply_schema, close_pool


def main() -> int:
    load_dotenv()
    settings = DatabaseSettings.from_env()
    print(f"Applying schema to: {settings.url}")
    try:
        apply_schema(settings)
    except ProviderError as exc:
        print(f"Schema apply FAILED: {exc}")
        if exc.cause:
            print(f"cause: {exc.cause}")
        return 1
    finally:
        # Migration uses a direct connection, but close the pool defensively so
        # every script honors the same "release DB resources on exit" boundary.
        close_pool()
    print("Schema applied (extension + tables ready).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
