"""Boot-time embedding check: refuse to start when the model's vectors do not fit.

Run by ``docker-entrypoint.sh`` after the schema is applied. The database
stores vectors of one fixed size (``vector(1024)`` in ``schema.sql``). A model
that returns another size would otherwise boot fine and then fail every sync
with a database error that does not name the cause.

Fatal: the model returns a different size than the database column, or the
remote embedder is missing its settings. Never fatal: the embedding service
cannot be reached (a vendor briefly down must not fail a deploy). Skipped for
the local backend, which would load the model just to check it.

Run by hand:
    python scripts/check_embedding_config.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.config.settings import EmbeddingSettings
from app.core.exceptions import ConfigurationError, EmbeddingProviderError


def check(provider, column_dim: int) -> str | None:
    """The problem that proves the config wrong, or None."""
    try:
        size = len(provider.embed(["Handbook startup check"])[0])
    except EmbeddingProviderError as exc:
        print(f"Embedding check: WARNING not verified, the service did not answer ({exc})")
        return None
    if size == column_dim:
        print(f"Embedding check: the model returns {size} numbers, as the database stores: ok")
        return None
    return (
        f"the model returns {size} numbers per text, but the database stores "
        f"{column_dim}. Use a model that returns {column_dim}; or, if your model "
        f"can shorten its vectors, set EMBEDDING_SEND_DIMENSIONS=true; or change "
        f"the database's vector size first (see 'Changing the embedding size' "
        f"in the install guide)."
    )


def _column_dim() -> int:
    from app.db.connection import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"
        ).fetchone()
    return row[0]


def main() -> int:
    load_dotenv()
    settings = EmbeddingSettings.from_env()
    if settings.backend != "remote":
        print(f"Embedding check: skipped (EMBEDDING_BACKEND={settings.backend})")
        return 0

    from app.db import close_pool
    from app.embeddings.factory import build_embedding_provider

    try:
        provider = build_embedding_provider(settings)
        problem = check(provider, _column_dim())
    except ConfigurationError as exc:
        problem = str(exc)
    finally:
        close_pool()
    if problem:
        print(f"Embedding check: FAILED {problem}")
        print("Embedding check: refusing to start.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
