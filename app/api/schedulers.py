"""``/schedulers``: recurring activity reports, self-service for any member.

Session-gated, not admin: a scheduler reads a connection the org already
set up and mails only its creator. ``org_id`` and ``user_id`` come from
the session, so nobody touches another person's schedulers.
"""

from __future__ import annotations

import re

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..db.connection import get_connection
from ..schedulers import reports as sched_reports
from ..schedulers import store as sched_store
from ..schedulers.prompts import CREATE_SCHEDULER_TOOL, build_setup_system_prompt
from ..schedulers.store import FREQUENCIES, SUPPORTED_PROVIDERS, SchedulerError
from ..security.rate_limit import limit_question
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


def _payload(scheduler, workspace_names: dict[str, str] | None = None) -> dict:
    names = workspace_names or {}
    return {
        "id": scheduler.id,
        "provider": scheduler.provider,
        "workspace_id": scheduler.workspace_id,
        # Resolved for display: a report card showing "Meeting notes" is
        # checkable by the reader in a way a UUID is not.
        "workspace_name": names.get(scheduler.workspace_id or ""),
        "frequency": scheduler.frequency,
        "prompt": scheduler.prompt,
        "status": scheduler.status,
        "last_run_at": scheduler.last_run_at,
        "next_run_at": scheduler.next_run_at,
        "last_error": scheduler.last_error,
        "created_at": scheduler.created_at,
    }


def _connected_providers(org_id: str, user_id: str) -> list[dict]:
    """Connections a scheduler can target (metadata only, never a token), per scope.

    ``scope="org"`` rows are org-wide; ``scope="workspace"`` rows come only for
    spaces the user is in (the ``workspace_members`` join, so others' ids never
    leave the DB). Limited to ``SUPPORTED_PROVIDERS``.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id::text, c.provider, c.external_workspace_name,
                   c.workspace_id::text, w.name, c.source_config
            FROM oauth_connections c
            LEFT JOIN workspaces w ON w.id = c.workspace_id
            LEFT JOIN workspace_members wm
                   ON wm.workspace_id = c.workspace_id AND wm.user_id = %s
            WHERE c.org_id = %s
              AND c.provider = ANY(%s)
              AND (c.workspace_id IS NULL OR wm.user_id IS NOT NULL)
            ORDER BY c.workspace_id NULLS FIRST, c.provider
            """,
            (user_id, org_id, list(SUPPORTED_PROVIDERS)),
        ).fetchall()
    return [
        {
            "id": row[0],
            "provider": row[1],
            "workspace_name": row[2],
            "scope": "workspace" if row[3] else "org",
            "space_id": row[3],
            "space_name": row[4],
            "topics": _topics(row[1], row[5]),
        }
        for row in rows
    ]


# What one connection covers (picked channels, authorized repos), offered as
# starting points.
MAX_TOPICS = 12


def _topics(provider: str, config) -> list[str]:
    """Names of what this connection reads, from its stored ``source_config``.

    Names only, never config or tokens. Linear, Notion and Drive have no stored
    subset, so they return nothing rather than a guess.
    """
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            return []
    if not isinstance(config, dict):
        return []

    if provider == "slack":
        names = config.get("channel_names") or {}
        ids = config.get("channel_ids") or []
        if isinstance(names, dict):
            # Keep the admin's picked order; fall back to the id so a
            # suggestion is never rendered as a bare "#".
            picked = [str(names.get(cid) or cid) for cid in ids]
        else:
            picked = [str(cid) for cid in ids]
        return picked[:MAX_TOPICS]

    if provider == "github":
        return [
            str(repo.get("full_name"))
            for repo in (config.get("repos") or [])
            if repo.get("full_name")
        ][:MAX_TOPICS]

    return []


def _spaces(org_id: str, user_id: str) -> list[dict]:
    """Every space this member could scope a report to.

    A space with no connections is listed with empty ``providers``, not dropped,
    so it does not silently vanish.
    """
    from ..workspaces.store import list_my_workspaces

    connections = _connected_providers(org_id, user_id)
    # Every connection in the org, schedulable or not — this is what the label
    # after a space name reports ("Meeting notes · Drive").
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT workspace_id::text, provider FROM oauth_connections "
            "WHERE org_id = %s",
            (org_id,),
        ).fetchall()
    all_by_space: dict[str | None, set[str]] = {}
    for space_id, provider in rows:
        all_by_space.setdefault(space_id, set()).add(provider)

    spaces = [
        {
            "id": None,
            # Matches the UI's first dropdown option exactly, so an error or
            # empty-state message naming the scope reads the same as the pick.
            "name": "Organisation",
            "scope": "org",
            "providers": [c["provider"] for c in connections if c["scope"] == "org"],
            "connected": sorted(all_by_space.get(None, set())),
        }
    ]
    for workspace in list_my_workspaces(org_id, user_id):
        spaces.append(
            {
                "id": workspace.id,
                "name": workspace.name,
                "scope": "workspace",
                "providers": [
                    c["provider"]
                    for c in connections
                    if c["space_id"] == workspace.id
                ],
                # Everything connected to the space (the label after its name).
                "connected": sorted(all_by_space.get(workspace.id, set())),
            }
        )
    return spaces


def _named_provider(prompt: str, available: list[str]) -> str | None:
    """The service the prompt NAMES, via the router's matcher.

    Checked before the probe: a Notion page ABOUT Linear can outscore Linear.
    """
    from ..agent.routing import named_provider

    return named_provider(prompt, available)


def _classify_provider(
    prompt: str, org_id: str, user_id: str, workspace_id: str | None
) -> str:
    """Which service this report is about, read from the prompt.

    Reuses ``agent.routing.choose_agent`` (tenant-scoped similarity, GitHub's
    named-repo rule), not an LLM: a hallucinated provider would become a
    standing wrong report.
    """
    available = [
        c["provider"]
        for c in _connected_providers(org_id, user_id)
        if c["space_id"] == workspace_id
    ]
    if not available:
        where = "that space" if workspace_id else "this organization"
        raise HTTPException(
            status_code=400,
            detail=f"Nothing that supports scheduled reports is connected for {where}.",
        )
    # One source in scope is not a classification problem, and skipping the
    # probe here saves an embedding on the overwhelmingly common tenant.
    if len(available) == 1:
        return available[0]

    named = _named_provider(prompt, available)
    if named is not None:
        logger.info("Scheduler provider named in the prompt: %s", named)
        return named

    from ..agent.routing import choose_agent

    decision = choose_agent(prompt, org_id, workspace_id=workspace_id)
    if decision.agent_key in available:
        logger.info(
            "Scheduler provider classified as %s (%s) for org %s",
            decision.agent_key,
            decision.reason,
            org_id,
        )
        return decision.agent_key

    # ``choose_agent`` falls back to a default agent when it has nothing to
    # measure; rather than guess the source every week, ask for one word.
    raise HTTPException(
        status_code=400,
        detail=(
            "Could not tell which app this report is about. Mention it in the "
            "prompt — for example "
            + " or ".join(sorted(available))
            + "."
        ),
    )


def _resolve_connection(
    org_id: str, user_id: str, provider: str, workspace_id: str | None
) -> str:
    """The connection id for ``provider`` in this scope, or 400.

    Resolved from the session's org, never the request. A space request is
    membership-checked and matched only to the space's own connection, never
    the company's.
    """
    if workspace_id:
        from ..core.exceptions import AuthError
        from ..workspaces.store import assert_member

        try:
            assert_member(workspace_id, org_id, user_id)
        except AuthError as exc:
            raise HTTPException(
                status_code=403, detail="Not a member of that space."
            ) from exc

    for connection in _connected_providers(org_id, user_id):
        if connection["provider"] == provider and connection["space_id"] == workspace_id:
            return connection["id"]
    where = "that space" if workspace_id else "this organization"
    raise HTTPException(
        status_code=400,
        detail=(
            f"{provider} is not connected for {where}, or does not "
            "support scheduled reports yet."
        ),
    )


@router.get("/connections")
def list_schedulable_connections(session: SessionClaims = Depends(get_session)):
    """Which services this member can build a scheduler against, by scope."""
    return {
        "connections": _connected_providers(session.org_id, session.user_id),
        "spaces": _spaces(session.org_id, session.user_id),
    }


@router.get("")
def list_my_schedulers(session: SessionClaims = Depends(get_session)):
    names = _workspace_names(session.org_id, session.user_id)
    return {
        "schedulers": [
            _payload(s, names)
            for s in sched_store.list_schedulers(session.org_id, session.user_id)
        ]
    }


class CreateSchedulerRequest(BaseModel):
    #: Optional: omitted means read it off the prompt (``_classify_provider``).
    provider: str | None = None
    frequency: str
    prompt: str
    #: None = the org-wide connection; a workspace id = that space's own.
    workspace_id: str | None = None


@router.post("", status_code=201)
def create(
    body: CreateSchedulerRequest, session: SessionClaims = Depends(get_session)
):
    return _payload(
        _create_scheduler_checked(
            session.org_id,
            session.user_id,
            body.provider,
            body.frequency,
            body.prompt,
            workspace_id=body.workspace_id,
        ),
        _workspace_names(session.org_id, session.user_id),
    )


def _workspace_names(org_id: str, user_id: str) -> dict[str, str]:
    from ..workspaces.store import list_my_workspaces

    return {w.id: w.name for w in list_my_workspaces(org_id, user_id)}


def _create_scheduler_checked(
    org_id: str,
    user_id: str,
    provider: str | None,
    frequency: str,
    prompt: str,
    workspace_id: str | None = None,
):
    """Validate then create; shared by the REST route and the chat flow.

    The chat flow's fields come from a model tool call, as untrusted as a body.
    """
    prompt = bounded(prompt.strip(), field="prompt", limit=MAX_PROMPT_CHARS)
    if frequency not in FREQUENCIES:
        raise HTTPException(
            status_code=400,
            detail=f"frequency must be one of {list(FREQUENCIES)}.",
        )
    if not provider:
        provider = _classify_provider(prompt, org_id, user_id, workspace_id)
    connection_id = _resolve_connection(org_id, user_id, provider, workspace_id)
    try:
        return sched_store.create_scheduler(
            org_id,
            user_id,
            connection_id,
            provider,
            frequency,
            prompt,
            workspace_id=workspace_id,
        )
    except SchedulerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _report_row(report) -> dict:
    """List shape: title, labels, when; never the body (only the detail route sends it)."""
    return {
        "id": report.id,
        "scheduler_id": report.scheduler_id,
        "provider": report.provider,
        "frequency": report.frequency,
        # The title is the standing request, in the reader's words.
        "title": report.prompt,
        "space_name": report.space_name,
        "item_count": len(report.items),
        "delivered": report.delivered_to is not None,
        "window_start": report.window_start,
        "window_end": report.window_end,
        "created_at": report.created_at,
        # All of this schedule's reports, of which the list shows the latest.
        "runs_total": report.runs_total,
    }


@router.get("/reports")
def list_my_reports(session: SessionClaims = Depends(get_session)):
    """Every report this member has been sent, newest first."""
    return {
        "reports": [
            _report_row(r)
            for r in sched_reports.list_reports(session.org_id, session.user_id)
        ]
    }


@router.get("/reports/{report_id}")
def get_my_report(report_id: str, session: SessionClaims = Depends(get_session)):
    """One full report.

    404 for someone else's: both scope columns are in the query, so a guessed id
    looks deleted.
    """
    report = sched_reports.get_report(session.org_id, session.user_id, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return {
        **_report_row(report),
        "report_text": report.report_text,
        # Rendered by the page from this structured data — the model never
        # wrote a URL, so none can be invented, dropped, or mangled.
        "items": report.items,
        "notes": report.notes,
    }


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
    return _payload(updated, _workspace_names(session.org_id, session.user_id))


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

    Stateless: the caller resends the history (three fields need no stored
    conversation). Returns ``{"done": true, "scheduler": ...}`` or
    ``{"done": false, "reply": ...}``.
    """
    limit_question(session)

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

    # The chat flow has no space slot, so only org-wide providers are offered.
    connected = [
        c["provider"]
        for c in _connected_providers(session.org_id, session.user_id)
        if c["scope"] == "org"
    ]

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

    # Tool arguments are untrusted: the model may name an unconnected provider
    # or invent a frequency.
    scheduler = _create_scheduler_checked(
        session.org_id,
        session.user_id,
        str(arguments.get("provider") or ""),
        str(arguments.get("frequency") or ""),
        str(arguments.get("prompt") or ""),
    )
    return {"done": True, "scheduler": _payload(scheduler)}
