"""``/schedulers`` — recurring activity reports, self-service for any member.

Every route is gated on ``get_session`` only, never ``require_admin``: a
scheduler reads a connection the org has *already* set up and mails only its
own creator, so it grants no access the member did not already have. Creating
one is meant to be an ordinary, repeatable action.

``org_id`` and ``user_id` always come from the signed session, never the
request body — the same rule as every other router here. A caller cannot
create, read, or delete a scheduler for anyone else, including inside their
own org.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db.connection import get_connection
from ..schedulers import store as sched_store
from ..schedulers.prompts import CREATE_SCHEDULER_TOOL, build_setup_system_prompt
from ..schedulers.store import FREQUENCIES, SUPPORTED_PROVIDERS, SchedulerError
from ..security.rate_limit import check_rate_limit
from .deps import SessionClaims, get_session
from .validation import bounded

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schedulers", tags=["schedulers"])

# A standing instruction, not an essay. Long enough for a real multi-clause
# request, short enough that it can't bloat every future report's prompt.
MAX_PROMPT_CHARS = 2000
# The setup chat is a three-slot conversation, so a long history means
# something has gone wrong (or is being abused to smuggle a large prompt).
MAX_SETUP_MESSAGES = 20
MAX_SETUP_MESSAGE_CHARS = 4000


def _payload(scheduler) -> dict:
    return {
        "id": scheduler.id,
        "provider": scheduler.provider,
        "frequency": scheduler.frequency,
        "prompt": scheduler.prompt,
        "status": scheduler.status,
        "last_run_at": scheduler.last_run_at,
        "next_run_at": scheduler.next_run_at,
        "last_error": scheduler.last_error,
        "created_at": scheduler.created_at,
    }


def _connected_providers(org_id: str) -> list[dict]:
    """Org-wide connections a scheduler can target, metadata only.

    Deliberately its own query rather than reusing ``list_connections``: that
    one is shaped for the admin Sources page (it returns reauth state and
    source_config) and is reached only behind ``require_admin``. A member
    needs strictly less — enough to pick a service — so this returns strictly
    less, and never a token.

    Phase 1 filters to the providers with a real "activity since T" fetcher.
    A Notion or Drive connection is genuinely present in the org but cannot
    be scheduled yet, and offering it would create a scheduler that fails
    every cycle.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id::text, provider, external_workspace_name "
            "FROM oauth_connections "
            "WHERE org_id = %s AND workspace_id IS NULL AND provider = ANY(%s) "
            "ORDER BY provider",
            (org_id, list(SUPPORTED_PROVIDERS)),
        ).fetchall()
    return [
        {"id": row[0], "provider": row[1], "workspace_name": row[2]} for row in rows
    ]


def _resolve_connection(org_id: str, provider: str) -> str:
    """The org-wide connection id for ``provider``, or 400.

    Resolved server-side from the session's org rather than taken from the
    request, so a client (or an LLM tool call) naming another tenant's
    connection id simply has no way to express it.
    """
    for connection in _connected_providers(org_id):
        if connection["provider"] == provider:
            return connection["id"]
    raise HTTPException(
        status_code=400,
        detail=(
            f"{provider} is not connected for this organization, or does not "
            "support scheduled reports yet."
        ),
    )


@router.get("/connections")
def list_schedulable_connections(session: SessionClaims = Depends(get_session)):
    """Which services this member can build a scheduler against."""
    return {"connections": _connected_providers(session.org_id)}


@router.get("")
def list_my_schedulers(session: SessionClaims = Depends(get_session)):
    return {
        "schedulers": [
            _payload(s)
            for s in sched_store.list_schedulers(session.org_id, session.user_id)
        ]
    }


class CreateSchedulerRequest(BaseModel):
    provider: str
    frequency: str
    prompt: str


@router.post("", status_code=201)
def create(
    body: CreateSchedulerRequest, session: SessionClaims = Depends(get_session)
):
    return _payload(
        _create_scheduler_checked(
            session.org_id, session.user_id, body.provider, body.frequency, body.prompt
        )
    )


def _create_scheduler_checked(
    org_id: str, user_id: str, provider: str, frequency: str, prompt: str
):
    """Validate then create. Shared by the REST route and the chat flow.

    Both entry points funnel through here on purpose: the chat flow's fields
    come from an LLM tool call, which is untrusted input in exactly the way a
    request body is. Letting it skip these checks is how a hallucinated
    provider would reach the table.
    """
    prompt = bounded(prompt.strip(), field="prompt", limit=MAX_PROMPT_CHARS)
    if frequency not in FREQUENCIES:
        raise HTTPException(
            status_code=400,
            detail=f"frequency must be one of {list(FREQUENCIES)}.",
        )
    connection_id = _resolve_connection(org_id, provider)
    try:
        return sched_store.create_scheduler(
            org_id, user_id, connection_id, provider, frequency, prompt
        )
    except SchedulerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class UpdateSchedulerRequest(BaseModel):
    frequency: str | None = None
    prompt: str | None = None


@router.patch("/{scheduler_id}")
def update(
    scheduler_id: str,
    body: UpdateSchedulerRequest,
    session: SessionClaims = Depends(get_session),
):
    if body.frequency is not None and body.frequency not in FREQUENCIES:
        raise HTTPException(
            status_code=400, detail=f"frequency must be one of {list(FREQUENCIES)}."
        )
    prompt = body.prompt
    if prompt is not None:
        prompt = bounded(prompt.strip(), field="prompt", limit=MAX_PROMPT_CHARS)
    try:
        updated = sched_store.update_scheduler(
            session.org_id,
            session.user_id,
            scheduler_id,
            frequency=body.frequency,
            prompt=prompt,
        )
    except SchedulerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Scheduler not found")
    return _payload(updated)


@router.delete("/{scheduler_id}", status_code=204)
def delete(scheduler_id: str, session: SessionClaims = Depends(get_session)):
    if not sched_store.delete_scheduler(
        session.org_id, session.user_id, scheduler_id
    ):
        raise HTTPException(status_code=404, detail="Scheduler not found")
    return None


# --------------------------------------------------------------------------
# Chat-driven setup
# --------------------------------------------------------------------------


class SetupMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class SetupChatRequest(BaseModel):
    messages: list[SetupMessage]


@router.post("/setup-chat")
def setup_chat(
    body: SetupChatRequest, session: SessionClaims = Depends(get_session)
):
    """One turn of the conversational setup flow.

    Stateless: the caller holds the history and resends it, so there is no new
    conversation table for what is a three-slot exchange the user completes in
    under a minute. Reusing ``app/memory/``'s machinery here would mean
    persisting, summarising and pruning a conversation that exists only to
    fill in three fields.

    Returns either ``{"done": true, "scheduler": ...}`` when the model had
    everything and the scheduler was created, or ``{"done": false, "reply":
    ...}`` with a follow-up question to show the user.
    """
    check_rate_limit(f"scheduler-setup:{session.org_id}:{session.user_id}")

    if not body.messages:
        raise HTTPException(status_code=400, detail="messages must not be empty")
    if len(body.messages) > MAX_SETUP_MESSAGES:
        raise HTTPException(
            status_code=400,
            detail=(
                "This setup conversation is too long. Start a new one and state "
                "the service, frequency, and what you want in one message."
            ),
        )
    for message in body.messages:
        bounded(
            message.content.strip(),
            field="message content",
            limit=MAX_SETUP_MESSAGE_CHARS,
        )

    connected = [c["provider"] for c in _connected_providers(session.org_id)]

    # Lazy import: keeps the LLM client out of module import time, matching how
    # githublive/credentials defer theirs.
    from ..core.exceptions import ProviderError
    from ..llm import build_aux_llm_provider

    messages = [
        {"role": "system", "content": build_setup_system_prompt(connected)},
        *({"role": m.role, "content": m.content} for m in body.messages),
    ]

    try:
        result = build_aux_llm_provider().generate_with_tools(
            messages, tools=[CREATE_SCHEDULER_TOOL], tool_choice="auto"
        )
    except ProviderError as exc:
        logger.warning("Scheduler setup chat failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Could not reach the assistant just now. Please try again.",
        ) from exc

    if not result.tool_calls:
        return {"done": False, "reply": result.text or ""}

    call = result.tool_calls[0]
    try:
        arguments = json.loads(call.arguments or "{}")
    except json.JSONDecodeError:
        # A malformed tool call is a model failure, not a user error — ask
        # again rather than surfacing a parse error.
        return {
            "done": False,
            "reply": "Sorry, I lost track of that. Which service, how often, "
            "and what should the report cover?",
        }

    # Everything below treats the tool arguments as untrusted: the model can
    # name a provider the org has not connected, or invent a frequency. Same
    # discipline as `resolve_repo` validating an LLM-supplied repo name before
    # any authenticated call.
    scheduler = _create_scheduler_checked(
        session.org_id,
        session.user_id,
        str(arguments.get("provider") or ""),
        str(arguments.get("frequency") or ""),
        str(arguments.get("prompt") or ""),
    )
    return {"done": True, "scheduler": _payload(scheduler)}
