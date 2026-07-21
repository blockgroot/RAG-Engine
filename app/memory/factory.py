"""Single construction point for the conversation store."""

from __future__ import annotations

from ..config.settings import DatabaseSettings
from .base import ConversationStore
from .pg_store import PgConversationStore


def build_conversation_store(
    db_settings: DatabaseSettings | None = None,
) -> ConversationStore:
    """Build the configured conversation store (Postgres-backed)."""
    return PgConversationStore(settings=db_settings)
