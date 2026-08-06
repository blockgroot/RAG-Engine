"""Postgres implementation of ``ConversationStore``.

Uses the same pooled ``get_connection`` and org-scoping conventions as the vector
store. Conversations and their turns cascade-delete with their organization.
"""

from __future__ import annotations

import json

from ..config.settings import DatabaseSettings
from ..core.exceptions import ProviderError
from ..db.connection import get_connection
from .base import ConversationContext, ConversationStore, RetrievedChunkRecord, Turn


class PgConversationStore(ConversationStore):
    """Conversation history backed by Postgres, org-scoped."""

    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self._settings = settings or DatabaseSettings.from_env()

    def create_conversation(self, org_id: str, workspace_id: str | None = None) -> str:
        with get_connection(self._settings) as conn:
            row = conn.execute(
                "INSERT INTO conversations (org_id, workspace_id) VALUES (%s::uuid, %s::uuid) "
                "RETURNING id",
                (org_id, workspace_id),
            ).fetchone()
        return str(row[0])

    def append_turn(self, conversation_id: str, question: str, answer: str) -> int:
        with get_connection(self._settings) as conn:
            org_row = conn.execute(
                "SELECT org_id FROM conversations WHERE id = %s::uuid",
                (conversation_id,),
            ).fetchone()
            if org_row is None:
                raise ProviderError(f"Unknown conversation_id: {conversation_id}")
            org_id = org_row[0]

            next_index = conn.execute(
                """
                SELECT COALESCE(MAX(turn_index), -1) + 1
                FROM conversation_turns WHERE conversation_id = %s::uuid
                """,
                (conversation_id,),
            ).fetchone()[0]

            conn.execute(
                """
                INSERT INTO conversation_turns
                    (conversation_id, org_id, turn_index, question, answer)
                VALUES (%s::uuid, %s, %s, %s, %s)
                """,
                (conversation_id, org_id, next_index, question, answer),
            )
        return int(next_index)

    def get_turns(self, conversation_id: str) -> list[Turn]:
        with get_connection(self._settings) as conn:
            rows = conn.execute(
                """
                SELECT turn_index, question, answer
                FROM conversation_turns
                WHERE conversation_id = %s::uuid
                ORDER BY turn_index
                """,
                (conversation_id,),
            ).fetchall()
        return [Turn(turn_index=r[0], question=r[1], answer=r[2]) for r in rows]

    def get_summary(self, conversation_id: str) -> str | None:
        with get_connection(self._settings) as conn:
            row = conn.execute(
                "SELECT summary FROM conversations WHERE id = %s::uuid",
                (conversation_id,),
            ).fetchone()
        return row[0] if row else None

    def get_context(self, conversation_id: str, recent_turns: int) -> ConversationContext:
        with get_connection(self._settings) as conn:
            summary_row = conn.execute(
                "SELECT summary FROM conversations WHERE id = %s::uuid",
                (conversation_id,),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT turn_index, question, answer
                FROM conversation_turns
                WHERE conversation_id = %s::uuid
                ORDER BY turn_index DESC
                LIMIT %s
                """,
                (conversation_id, recent_turns),
            ).fetchall()

        summary = summary_row[0] if summary_row else None
        recent = [Turn(turn_index=r[0], question=r[1], answer=r[2]) for r in reversed(rows)]
        return ConversationContext(summary=summary, recent_turns=recent)

    def set_summary_and_prune(
        self, conversation_id: str, summary: str, keep_recent: int
    ) -> None:
        with get_connection(self._settings) as conn:
            conn.execute(
                "UPDATE conversations SET summary = %s WHERE id = %s::uuid",
                (summary, conversation_id),
            )
            # Keep only the most recent `keep_recent` turns; delete the rest.
            conn.execute(
                """
                DELETE FROM conversation_turns
                WHERE conversation_id = %s::uuid
                  AND turn_index <= (
                      SELECT COALESCE(MAX(turn_index), -1) - %s
                      FROM conversation_turns WHERE conversation_id = %s::uuid
                  )
                """,
                (conversation_id, keep_recent, conversation_id),
            )

    # -- Phase 8: last-turn retrieval, for the cheap retrieval-reuse check ----

    def set_last_retrieval(
        self, conversation_id: str, org_id: str, chunks: list[RetrievedChunkRecord]
    ) -> None:
        # One row per conversation (upsert): only the latest turn's chunks matter.
        # Stored as JSON text — no vector columns; embeddings are recomputed on read.
        payload = json.dumps(
            [
                {
                    "content": c.content,
                    "document_id": c.document_id,
                    "chunk_index": c.chunk_index,
                    "org_id": c.org_id,
                    "document_title": c.document_title,
                }
                for c in chunks
            ]
        )
        with get_connection(self._settings) as conn:
            conn.execute(
                """
                INSERT INTO conversation_last_retrieval (conversation_id, org_id, chunks)
                VALUES (%s::uuid, %s::uuid, %s)
                ON CONFLICT (conversation_id)
                DO UPDATE SET chunks = EXCLUDED.chunks, updated_at = now()
                """,
                (conversation_id, org_id, payload),
            )

    def get_last_retrieval(self, conversation_id: str) -> list[RetrievedChunkRecord]:
        with get_connection(self._settings) as conn:
            row = conn.execute(
                "SELECT chunks FROM conversation_last_retrieval WHERE conversation_id = %s::uuid",
                (conversation_id,),
            ).fetchone()
        if not row or not row[0]:
            return []
        # The column is TEXT holding a JSON array; be tolerant if a driver hands
        # back an already-decoded list.
        raw = row[0]
        items = json.loads(raw) if isinstance(raw, str) else raw
        return [
            RetrievedChunkRecord(
                content=item["content"],
                document_id=item["document_id"],
                chunk_index=item["chunk_index"],
                org_id=item["org_id"],
                document_title=item.get("document_title"),
            )
            for item in items
        ]
