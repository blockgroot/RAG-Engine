"""Ask a friend, end to end: who may receive, whether the asker must tick,
then summary, delivery, and the one-recipient record.

Everything identifying comes from the session and the DB: the conversation must
be the asker's, the turn must be one that offered Ask a friend, and the
recipient must be a member of the chat's scope. Refusals are HTTP errors with a
short code the web app reads.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException

from ..auth.session import SessionClaims
from ..core.exceptions import AuthError
from ..db import get_connection
from ..memory.conversations import (
    ConversationTurnRow, claim_ask_friend, get_conversation_turns, release_ask_friend,
)
from ..security.rate_limit import limit_ask_friend, limit_email, limit_question
from ..sources.slack_utils import lookup_user_id
from ..workspaces import assert_member
from .deliver import send_email, send_slack, slack_token
from .privacy import needs_approval
from .summary import summarize


def scope_people(org_id: str, workspace_id: str | None) -> list[dict]:
    """Everyone in the chat's scope: the space's members, or the whole company.
    ``name`` is the display name they chose, else their email (never a guess)."""
    with get_connection() as conn:
        if workspace_id:
            rows = conn.execute(
                "SELECT u.id::text, u.email, u.display_name FROM workspace_members wm "
                "JOIN users u ON u.id = wm.user_id JOIN workspaces w ON w.id = wm.workspace_id "
                "WHERE wm.workspace_id = %s AND w.org_id = %s",
                (workspace_id, org_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id::text, email, display_name FROM users WHERE org_id = %s", (org_id,)
            ).fetchall()
    people = [{"user_id": r[0], "email": r[1], "name": (r[2] or "").strip() or r[1]} for r in rows]
    return sorted(people, key=lambda p: p["name"].lower())


def _scope(session: SessionClaims, workspace_id: str | None) -> None:
    if workspace_id:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail="not_a_member") from exc


def people(session: SessionClaims, workspace_id: str | None) -> list[dict]:
    _scope(session, workspace_id)
    return [p for p in scope_people(session.org_id, workspace_id) if p["user_id"] != session.user_id]


def _context(session: SessionClaims, body: dict) -> tuple[list[ConversationTurnRow], dict, dict]:
    """(turns up to the refused one, recipient, asker), or the HTTP refusal."""
    workspace_id = body.get("workspace_id") or None
    _scope(session, workspace_id)
    turns = get_conversation_turns(
        conversation_id=str(body.get("conversation_id") or ""), org_id=session.org_id,
        user_id=session.user_id, workspace_id=workspace_id,
    ) if body.get("conversation_id") else []
    question = body.get("question")
    wanted = body.get("turn_index")
    if isinstance(wanted, int) and not isinstance(wanted, bool):
        # The bubble clicked: Deep analysis re-asks the same question, so the text alone is ambiguous.
        at = next((i for i, t in enumerate(turns) if t.turn_index == wanted), None)
    else:
        at = next((i for i in range(len(turns) - 1, -1, -1) if turns[i].question == question), None)
    if at is None or not (turns[at].meta or {}).get("ask_friend"):
        raise HTTPException(status_code=404, detail="not_found")
    everyone = {p["user_id"]: p for p in scope_people(session.org_id, workspace_id)}
    recipient = everyone.get(str(body.get("recipient_user_id") or ""))
    asker = everyone.get(session.user_id)
    if recipient is None or asker is None or recipient["user_id"] == session.user_id:
        raise HTTPException(status_code=403, detail="not_in_scope")
    return turns[: at + 1], recipient, asker


def _slack_ready(session: SessionClaims, body: dict, recipient: dict) -> str | None:
    """The bot token when this colleague can be reached on Slack, else None."""
    token = slack_token(session.org_id, body.get("workspace_id") or None)
    return token if token and lookup_user_id(token, recipient["email"]) else None


def check(session: SessionClaims, body: dict) -> dict:
    turns, recipient, _ = _context(session, body)
    return {
        "needs_approval": needs_approval(session.org_id, turns, recipient["email"]),
        "slack_available": _slack_ready(session, body, recipient) is not None,
    }


def _summary(turns: list[ConversationTurnRow], question: str, model: str | None,
             session: SessionClaims) -> str:
    """The asker's picked model writes it, charged to them like any question."""
    from ..llm import build_llm_provider
    from ..llm.routed import use_model
    from ..security.ai_spend import charge_to

    use_model(model, org_id=session.org_id)
    charge_to(session.user_id, session.org_id)
    return summarize(turns, question, build_llm_provider())


def send(session: SessionClaims, body: dict) -> dict:
    turns, recipient, asker = _context(session, body)
    question = turns[-1].question
    if (turns[-1].meta or {}).get("ask_friend_sent"):
        raise HTTPException(status_code=409, detail="already_sent")
    via = body.get("via")
    if via not in ("slack", "email"):
        raise HTTPException(status_code=422, detail="via must be slack or email")
    if needs_approval(session.org_id, turns, recipient["email"]) and body.get("approve_private") is not True:
        raise HTTPException(status_code=409, detail="approval_required")
    token = _slack_ready(session, body, recipient) if via == "slack" else None
    if via == "slack" and token is None:
        raise HTTPException(status_code=409, detail="slack_unavailable")
    model = body.get("model") if isinstance(body.get("model"), str) else None
    limit_ask_friend(session.user_id)
    if via == "email":
        limit_email(f"user:{session.user_id}")
    limit_question(session, model)

    entry = {"to_name": recipient["name"], "via": via,
             "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    claim = dict(conversation_id=str(body["conversation_id"]), org_id=session.org_id,
                 user_id=session.user_id, turn_index=turns[-1].turn_index)
    if not claim_ask_friend(**claim, entry=entry):
        raise HTTPException(status_code=409, detail="already_sent")
    delivered = False
    try:
        summary = _summary(turns, question, model, session)
        if via == "slack":
            send_slack(token, recipient["email"], asker["email"], asker["name"], summary)
        else:
            send_email(recipient["email"], asker["email"], asker["name"], summary)
        delivered = True
    except Exception as exc:  # noqa: BLE001 - any failure: nothing was sent, say so
        raise HTTPException(status_code=502, detail="delivery_failed") from exc
    finally:
        if not delivered:
            release_ask_friend(**claim)  # the asker may try again
    return {"sent": entry}
