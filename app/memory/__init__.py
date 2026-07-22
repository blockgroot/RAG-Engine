"""Conversation memory (Phase 5).

Public API:
    from app.memory import build_conversation_store
    memory = build_conversation_store()
    cid = memory.create_conversation(org_id)
    memory.append_turn(cid, "question", "answer")
    ctx = memory.get_context(cid, recent_turns=4)
"""

from .base import ConversationStore, Turn, ConversationContext, RetrievedChunkRecord
from .pg_store import PgConversationStore
from .factory import build_conversation_store

__all__ = [
    "ConversationStore",
    "Turn",
    "ConversationContext",
    "RetrievedChunkRecord",
    "PgConversationStore",
    "build_conversation_store",
]
