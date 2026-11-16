"""Container boot: schema and config checks, once per change, in one process.

``docker-entrypoint.sh`` used to run three Python processes before the API
(schema, LLM check, embedding check). On Render's free plan the API sleeps
after 15 idle minutes and every wake-up repeated all three on a tenth of a CPU,
for nothing: a wake-up is the same image with the same settings.

The schema text and the checked settings are hashed; the hash is a
``schema_marks`` row (``boot:<hash>``). When the row is there, this boot
changes nothing and the checks already passed, so both are skipped. A deploy
with a new schema, model, key or embedding setting has a new hash and runs
everything, and a failed check still fails the deploy (exit 1) before the row
is written. Only the hash is stored, never a setting.

Run by hand:
    python scripts/boot.py
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

import check_embedding_config  # noqa: E402
import check_llm_config  # noqa: E402
import init_db  # noqa: E402
from app.config.settings import DatabaseSettings, EmbeddingSettings, LLMSettings  # noqa: E402
from app.db.migrate import SCHEMA_PATH  # noqa: E402

MARK_PREFIX = "boot:"


def boot_key() -> str:
    """What the schema step and the two checks depend on, hashed."""
    digest = hashlib.sha256(SCHEMA_PATH.read_bytes())
    digest.update(repr(LLMSettings.from_env()).encode())
    digest.update(repr(EmbeddingSettings.from_env()).encode())
    return MARK_PREFIX + digest.hexdigest()[:32]


def _already_booted(url: str, key: str) -> bool:
    """True when this exact schema and settings were applied and checked."""
    import psycopg

    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            return conn.execute(
                "SELECT 1 FROM schema_marks WHERE name = %s", (key,)
            ).fetchone() is not None
    except psycopg.Error:
        return False  # a fresh database has no schema_marks yet: do the full boot


def _remember(url: str, key: str) -> None:
    """Keep only this boot's row: an older one can never match again."""
    import psycopg

    with psycopg.connect(url, connect_timeout=10) as conn:
        conn.execute(
            "DELETE FROM schema_marks WHERE name LIKE %s AND name <> %s",
            (MARK_PREFIX + "%", key),
        )
        conn.execute(
            "INSERT INTO schema_marks (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (key,)
        )


def main() -> int:
    load_dotenv()
    url = DatabaseSettings.from_env().url or ""
    key = boot_key()
    if url and _already_booted(url, key):
        print("Boot: schema and settings unchanged since the last boot; skipping schema and checks.")
        return 0
    for step in (init_db.main, check_llm_config.main, check_embedding_config.main):
        if step() != 0:
            return 1
    try:
        _remember(url, key)
    except Exception as exc:  # noqa: BLE001 - only costs the next boot its shortcut
        print(f"Boot: could not record this boot ({exc}); the next one runs the checks again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
