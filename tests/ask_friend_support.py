"""Shared rows for the Ask a friend tests (staging DB)."""

from __future__ import annotations

import json
import uuid

from app.db import get_connection


def make_org() -> str:
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"askfriend-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    return str(row[0])


def make_user(org_id: str, *, name: str | None = None, email: str | None = None) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO users (email, org_id, role, display_name) VALUES (%s, %s, 'member', %s) "
            "RETURNING id",
            (email or f"{uuid.uuid4().hex[:8]}@example.com", org_id, name),
        ).fetchone()
        conn.commit()
    return str(row[0])


def make_conversation(org_id: str, user_id: str, workspace_id: str | None = None) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO conversations (org_id, user_id, workspace_id) VALUES (%s, %s, %s) RETURNING id",
            (org_id, user_id, workspace_id),
        ).fetchone()
        conn.commit()
    return str(row[0])


def add_turn(conversation_id: str, org_id: str, question: str, answer: str,
             *, meta: dict | None = None, cited: list | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO conversation_turns (conversation_id, org_id, turn_index, question, answer, meta, cited) "
            "VALUES (%s, %s, (SELECT coalesce(max(turn_index), -1) + 1 FROM conversation_turns "
            "WHERE conversation_id = %s::uuid), %s, %s, %s::jsonb, %s::jsonb)",
            (conversation_id, org_id, conversation_id, question, answer,
             json.dumps(meta) if meta is not None else None, json.dumps(cited or [])),
        )
        conn.commit()


def turn_meta(conversation_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT meta FROM conversation_turns WHERE conversation_id = %s::uuid "
            "ORDER BY turn_index DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
    return row[0] if row else None
