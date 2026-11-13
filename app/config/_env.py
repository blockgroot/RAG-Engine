"""Environment readers shared by every settings module."""

from __future__ import annotations

import os


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    # Empty is unset: the self-hosted .env lists optional switches with no
    # value, and an empty RETRIEVAL_RERANK_ENABLED= must not turn ranking off.
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _oauth_redirect(name: str, provider: str) -> str | None:
    """``name`` if set, else ``FRONTEND_URL`` + ``/api`` for ``provider``'s sign-in return.

    One address per install, not one per tool.
    """
    frontend = os.getenv("FRONTEND_URL")
    return os.getenv(name) or (
        f"{frontend.rstrip('/')}/api/auth/{provider}/callback" if frontend else None
    )
