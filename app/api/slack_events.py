"""Ask Handbook from inside Slack (Slack Events API receiver).

Why this exists
---------------
Every question so far required opening the web app. The content people ask
about already lives in Slack, and so do the people — so the cheapest way to
make Handbook useful is to answer where the question is already being typed.
The web app keeps what a console is for (connections, spaces, charts,
schedulers); asking moves to Slack.

This is an ADAPTER, not a new agent. The routing graph, the confidence gate,
the strict prompt and conversation memory are all reused untouched — nothing
here can weaken a grounding guarantee, because nothing here decides an answer.

Two scopes, and the difference is a permission decision
-------------------------------------------------------
- **In a channel**: answers from THAT channel only (`documents.tags` carries
  `slack:channel:<id>`). Everyone in the room can already scroll up and read
  those messages, so a channel-scoped answer discloses nothing new. Answering
  a channel from Drive, Notion or a *different* channel would be a broadcast
  of content some people present may not be able to open — which is exactly
  the parity gap this product has, so the bot must not create a new way to hit
  it.
- **In a DM**: answers from everything the asker could see in the web app.
  Private, one person, identical to what they would get by opening Handbook.

Identity
--------
Slack gives us a `team_id` and a user id. `team_id` is already stored as
`oauth_connections.external_workspace_id` (see auth/slack_oauth.py), so the
org is one query on a column that already exists. The user id becomes an email
via `users.info` (the `users:read.email` scope has always been requested), and
the email becomes a Handbook user. No account ⇒ we say so; we NEVER answer
without resolving a person, because org-scoped content requires knowing which
org, and "some human in this Slack" is not an authorization.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from ..agent.routing import _NO_MATCH, choose_agent, choose_scope
from ..feedback import record_gap
from ..auth.credentials import get_live_connection_token
from ..auth.users import get_user_by_email
from ..config.settings import SlackSettings
from ..core.exceptions import ProviderError
from ..db.connection import get_connection
from ..sources.slack_utils import channel_tag, post_message
from .deps import get_slack_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/slack", tags=["slack"])

# Slack's own replay window. A signature is only accepted inside it, so a
# captured request cannot be replayed tomorrow.
_MAX_SIGNATURE_AGE_SECONDS = 60 * 5

_NO_ACCOUNT = (
    "You don't have a Handbook account yet, so I can't look anything up for you. "
    "Ask an admin to invite you."
)
_ERROR = "Something went wrong answering that. Please try again in a moment."
_NOT_CONNECTED = (
    "This channel isn't connected to Handbook yet, so I can't read its history. "
    "An admin can add it under Sources → Slack. You can also DM me — I can answer "
    "from every source your company has connected."
)


def _verify(body: bytes, timestamp: str | None, signature: str | None, secret: str) -> bool:
    """Constant-time check that this request really came from Slack."""
    if not timestamp or not signature:
        return False
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False
    if age > _MAX_SIGNATURE_AGE_SECONDS:
        return False
    expected = "v0=" + hmac.new(
        secret.encode(), b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


#: ``_scope_for_channel`` found no scope indexing that channel. A distinct
#: sentinel because ``None`` is a VALID scope (org-wide), so "not found" and
#: "found, org-wide" cannot share a return value.
_NO_SCOPE = object()


def _org_for_team(team_id: str) -> tuple[str, str | None] | None:
    """Resolve a Slack team id to ``(org_id, workspace_id)`` for the TOKEN.

    This picks which connection's token posts the reply, NOT what the answer
    may read -- those are two different questions and conflating them is what
    made a company-wide bot answer from one private space. Any connection for
    this team can post (it is the same Slack workspace), so org-wide first
    purely for determinism.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id::text, workspace_id::text FROM oauth_connections "
            "WHERE provider = 'slack' AND external_workspace_id = %s "
            "AND NOT needs_reauth "
            "ORDER BY workspace_id NULLS FIRST LIMIT 1",
            (team_id,),
        ).fetchone()
    return (row[0], row[1]) if row else None


def _scope_for_channel(org_id: str, tag: str):
    """Which scope indexes this channel: ``None`` (org-wide), a space id, or ``_NO_SCOPE``.

    A channel's content lives wherever it was CONNECTED, and that is not
    necessarily where the token came from: a company can connect its public
    channels org-wide while a private channel stays inside one space. Asking
    the documents directly is what keeps those two apart -- resolving the scope
    from the connection instead would answer a private channel from the
    org-wide corpus (finding nothing) or, worse, require indexing that private
    channel org-wide to make the bot work, which publishes it to everyone.

    Org-wide wins a tie: a channel indexed in both places is already
    company-readable, so the broader copy is not a wider disclosure.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT workspace_id::text FROM documents "
            "WHERE org_id = %s::uuid AND tags && ARRAY[%s] "
            "GROUP BY workspace_id ORDER BY workspace_id NULLS FIRST LIMIT 1",
            (org_id, tag),
        ).fetchone()
    if row is None:
        return _NO_SCOPE
    return row[0]


def _slack_email(token: str, user_id: str) -> str | None:
    from ..sources.slack_utils import _get  # local: same module, private helper

    try:
        info = _get(token, "users.info", {"user": user_id})
    except Exception as exc:  # noqa: BLE001 - an identity miss is not fatal
        logger.warning("slack.bot users.info failed for %s: %s", user_id, exc)
        return None
    return ((info.get("user") or {}).get("profile") or {}).get("email")


#: Agent key -> what a person calls that source. The agent key is ours; nobody
#: in Slack knows what "google" or "github_live" means.
_SOURCE_LABELS = {
    "notion": "Notion",
    "google": "Google Drive",
    "slack": "Slack",
    "linear": "Linear",
    "github": "GitHub",
    "github_live": "GitHub",
    "insights": "Charts",
    "workspace": "Connected documents",
    "policy": "Company documents",
}


def _to_slack_mrkdwn(text: str) -> str:
    """Rewrite the model's Markdown as Slack mrkdwn.

    Slack does NOT render Markdown: `**bold**` shows its asterisks, `- item`
    stays a hyphen, and `### Heading` prints the hashes. The prompt produces
    Markdown for the web UI, so converting here is right -- asking the model
    for a per-surface format would make the answer's shape depend on where it
    was asked, and it would forget.
    """
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        # Headings become bold lines: Slack has no heading syntax at all.
        heading = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if heading:
            out.append(f"{indent}*{heading.group(1).strip()}*")
            continue
        # "- " / "* " -> a real bullet. Numbered lists already read fine.
        bullet = re.match(r"^[-*]\s+(.*)$", stripped)
        if bullet:
            out.append(f"{indent}•   {bullet.group(1)}")
            continue
        out.append(line)
    body = "\n".join(out)
    # **bold** -> *bold*. Done after the bullet pass so a "* " list marker at
    # the start of a line is never mistaken for an emphasis delimiter.
    body = re.sub(r"\*\*(.+?)\*\*", r"*\1*", body, flags=re.S)
    return body


def _dm_scopes(org_id: str, user_id: str) -> list[tuple[str | None, str]]:
    """Org-wide plus every space this person belongs to, as ``(id, label)``.

    Membership is READ, never inferred: `list_my_workspaces` is the same query
    the app uses, so a DM can only ever reach a space the asker is already in.
    """
    from ..workspaces import list_my_workspaces

    scopes: list[tuple[str | None, str]] = [(None, "Company")]
    try:
        scopes += [(w.id, w.name) for w in list_my_workspaces(org_id, user_id)]
    except Exception:  # noqa: BLE001 - a DM must still answer org-wide
        logger.warning("slack.bot could not list spaces for %s", user_id, exc_info=True)
    return scopes


def _answer(
    question: str,
    org_id: str,
    workspace_id: str | None,
    tags: list[str] | None,
    scope_label: str | None = None,
    user_id: str | None = None,
) -> str:
    """Run the existing pipeline. ``tags`` set => channel-scoped Slack only.

    ``scope_label`` names where the answer was allowed to look ("Company",
    "Meeting notes"); when given, the reply is prefixed with that plus the
    source that answered. Omitted for a channel question, where the asker is
    standing in the only place it could have come from.

    ``user_id`` is the asker's Handbook account, carried only so an unanswered
    question is logged as a documentation gap with a person attached -- "nine
    people asked this" is the line that gets a document written, and it is
    COUNT(DISTINCT user_id). Slack has no thumbs yet, so this surface writes
    gaps and never ratings.
    """
    if tags:
        # Pinned to Slack and filtered to one channel. Goes through the agent's
        # pipeline rather than the routing graph because `Agent.answer` has no
        # tag argument -- and it must not gain one: a tag filter is meaningful
        # for exactly this caller, and every other agent would have to ignore it.
        result = get_slack_agent().pipeline.answer(
            question, org_id=org_id, workspace_id=workspace_id, tags=tags
        )
        if not result.answered:
            record_gap(
                org_id=org_id,
                question=question,
                resolved_question=result.resolved_question,
                answer=result.answer,
                workspace_id=workspace_id,
                user_id=user_id,
                agent="slack",
                surface="slack",
                gate_score=result.top_score,
            )
        return _to_slack_mrkdwn(result.answer)

    decision = choose_agent(question, org_id, workspace_id=workspace_id)
    from ..agent.orchestration import build_agent_graph
    from .chat import _agent_getters

    # The chart spec has to be carried through exactly as the web chat does.
    # `choose_agent` can route to InsightsAgent AND resolve the spec in one
    # step, so dropping the spec sends a routed chart question to an agent
    # holding nothing -- which answers "I can't chart that", a flat denial of
    # something the router had already resolved.
    spec = getattr(decision, "chart_spec", None)
    chart_spec = (
        {
            "metric": spec.metric,
            "group_by": spec.group_by,
            "period": spec.period,
            "chart": spec.chart,
        }
        if spec is not None
        else None
    )

    state = build_agent_graph(_agent_getters()).invoke(
        {
            "question": question,
            "org_id": org_id,
            "conversation_id": None,
            "workspace_id": workspace_id,
            "requested_agent": decision.agent_key,
            "stream": False,
            "chart_spec": chart_spec,
            "chart_refusal": getattr(decision, "chart_refusal", None),
            # Deliberately NOT the asker's real role: Slack has no owner-only
            # surface, and the gated metrics (Forms sentiment) exist precisely
            # so the people they are collected on cannot read them. Answering
            # "member" keeps the floor here and can only ever omit a panel.
            "role": "member",
        }
    )
    response = state["response"]
    # Same gap log as the web chat, for the same reason and at the same edge:
    # a question asked in Slack that nothing could answer is the same missing
    # document as one asked in the app, and an admin reading the gap list must
    # not have to know which box it was typed into.
    # `getattr` rather than attribute access, like `_with_chart_values` just
    # below: an agent node is free to return any response shape, and a missing
    # diagnostic field must cost the gap log, never the answer. Absent =>
    # treated as grounded, so a gap is only ever recorded when we KNOW there
    # was one.
    if not getattr(response, "grounded", True):
        record_gap(
            org_id=org_id,
            question=question,
            resolved_question=getattr(response, "resolved_question", None),
            answer=getattr(response, "answer", None),
            workspace_id=workspace_id,
            user_id=user_id,
            agent=decision.agent_key,
            surface="slack",
            gate_score=getattr(response, "top_score", None),
        )
    body = _to_slack_mrkdwn(_with_chart_values(response))
    if scope_label is None:
        return body
    # WHICH source and WHICH scope, above the answer. With one box answering
    # from a company's Notion and from several private spaces, "where did this
    # come from?" is not answerable from the text -- and an answer whose origin
    # cannot be checked is the failure this whole codebase is arranged against.
    source = _SOURCE_LABELS.get(decision.agent_key, decision.agent_key)
    return f"_{source} · {scope_label}_\n{body}"


def _count(value) -> str:
    """Render a metric value. Counts are whole things, never "2.0".

    SQL aggregates come back as float/Decimal, and every registry metric counts
    REAL things -- issues, commits, pages -- so a decimal point is not a more
    precise answer, it is a wrong one. Same reasoning as `Chart.tsx::axisTicks`
    refusing fractional ticks.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else f"{number:g}"


def _with_chart_values(response) -> str:
    """Append a chart's numbers to its caption as text.

    `AgentResponse.chart` carries the counted rows, and the web UI draws them;
    Slack has no canvas. The caption alone ("Commits by author, last quarter")
    is a title with no data under it, which reads as the bot ignoring the
    question -- so the buckets are printed. Deliberately NOT an image: a PNG of
    numbers cannot be scrolled back to or re-scoped, which is the same reason
    `app/insights/` renders SVG rather than generating pictures.
    """
    chart = getattr(response, "chart", None)
    points = (chart or {}).get("points") if isinstance(chart, dict) else None
    if not points:
        return response.answer

    lines = []
    for point in points[:10]:
        label = point.get("group") or point.get("bucket") or ""
        value = point.get("value")
        if label and value is not None:
            lines.append(f"•   {label}: {_count(value)}")
    if not lines:
        return response.answer
    more = "" if len(points) <= 10 else f"\n…and {len(points) - 10} more"
    return f"{response.answer}\n" + "\n".join(lines) + more


def _handle(event: dict, team_id: str) -> None:
    """Resolve who asked, answer, and post back. Runs AFTER Slack was acked."""
    channel = event.get("channel")
    slack_user = event.get("user")
    text = (event.get("text") or "").strip()
    # Reply IN the channel for a top-level question, and inside the thread only
    # when the question was already in one. Falling back to the message's own
    # `ts` (the obvious default) threads every answer under its question, which
    # hides it behind a "1 reply" link the asker has to open -- two places to
    # look for one answer. Staying in an existing thread is different: there
    # the conversation already lives there, and answering outside it would pull
    # the reply away from its question.
    thread_ts = event.get("thread_ts")
    if not channel or not slack_user or not text:
        return

    scope = _org_for_team(team_id)
    if scope is None:
        logger.info("slack.bot: no connection for team %s", team_id)
        return
    org_id, token_workspace_id = scope

    try:
        token = get_live_connection_token(org_id, "slack", workspace_id=token_workspace_id)
    except ProviderError as exc:
        logger.warning("slack.bot token unavailable for org %s: %s", org_id, exc)
        return

    email = _slack_email(token, slack_user)
    user = get_user_by_email(email) if email else None
    if user is None or user.org_id != org_id:
        post_message(token, channel, _NO_ACCOUNT, thread_ts)
        return

    # THE SEPARATION OF CONCERNS, and it is a privacy boundary rather than a
    # convenience:
    #
    # * A DM is a COMPANY-WIDE surface -- every person in the Slack workspace
    #   can open one -- so it reads org-wide content ONLY, never a space's.
    #   Answering a DM from a space would hand that space's private content to
    #   anyone in Slack, which is precisely what a space exists to prevent.
    # * A channel question reads THAT CHANNEL, in whichever scope indexes it.
    #   Everyone in the room can already scroll up and read those messages, so
    #   a channel-scoped answer discloses nothing new -- and this is what lets
    #   a private channel stay connected to one space instead of having to be
    #   indexed org-wide (publishing it to the whole company) just to make the
    #   bot answer in it.
    #
    # `channel_type == "im"` is how Slack marks a direct message.
    if event.get("channel_type") == "im":
        # A DM is the PERSON's surface, not a space's: they legitimately see
        # org-wide content and every space they belong to, so answering only
        # org-wide would refuse questions whose answer they can read in the
        # app. The corpus picks which scope -- the same principle the router
        # already uses for sources ("the corpus answers which one resembles
        # this") -- and only the caller's OWN scopes are offered, so this can
        # never reach a space they are not a member of.
        scopes = _dm_scopes(org_id, user.id)
        answer_workspace_id, tags = None, None
        scope_label = "Company"
        if len(scopes) > 1:
            # The counterpart to `choose_agent`, and the same router module --
            # a DM is the one surface with no UI to say which space it means.
            best = choose_scope(org_id, scopes, text)
            if best is not _NO_MATCH:
                answer_workspace_id = best
                scope_label = next(
                    (name for sid, name in scopes if sid == best), "Company"
                )
    else:
        tag = channel_tag(channel)
        resolved = _scope_for_channel(org_id, tag)
        # "This channel is not connected" is a different FACT from "nothing
        # here answers that", and only the first tells anyone what to DO about
        # it. The bot can be invited to any channel, so this is the common
        # case, not an edge one -- and the RAG fallback can only ever hedge
        # ("it MAY have been discussed in a channel that isn't connected")
        # because retrieval cannot distinguish an unindexed channel from an
        # unanswered question. Checked BEFORE the pipeline, so an unconnected
        # channel also costs no LLM call.
        if resolved is _NO_SCOPE:
            post_message(token, channel, _NOT_CONNECTED, thread_ts)
            return
        answer_workspace_id, tags = resolved, [tag]
        # No label in a channel: the asker is standing in the only place the
        # answer could have come from, so naming it is noise.
        scope_label = None

    try:
        answer = _answer(
            text, org_id, answer_workspace_id, tags, scope_label, user_id=user.id
        )
    except Exception as exc:  # noqa: BLE001 - a failed answer must still reply
        logger.warning("slack.bot answer failed for org %s: %s", org_id, exc, exc_info=True)
        answer = _ERROR
    post_message(token, channel, answer, thread_ts)


@router.post("/events")
async def slack_events(
    request: Request,
    background: BackgroundTasks,
    x_slack_request_timestamp: str | None = Header(default=None),
    x_slack_signature: str | None = Header(default=None),
    x_slack_retry_num: str | None = Header(default=None),
):
    """Receive one Slack event. Acks immediately; answers in the background.

    Slack gives a handler THREE SECONDS and retries on timeout, while one
    grounded answer is ~6 LLM calls. Doing the work inline would therefore not
    just be slow -- it would deliver the same answer several times.
    """
    secret = SlackSettings.from_env().signing_secret
    if not secret:
        # Same posture as INTERNAL_TICK_SECRET: an unconfigured secret closes
        # the route rather than leaving an unauthenticated one open.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    body = await request.body()
    if not _verify(body, x_slack_request_timestamp, x_slack_signature, secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad signature")

    payload = await request.json()

    # One-time handshake when the Request URL is saved in the Slack app config.
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}

    # Slack retries up to 3 times when an ack misses its 3-second deadline,
    # and on a free instance a cold start misses it routinely. The first
    # delivery has ALREADY started answering by then, so honouring a retry
    # posts the same answer two or three times. Dropping retries can only cost
    # an answer whose original delivery genuinely died -- asking again is one
    # message, while a channel of triplicated replies is the failure people
    # actually notice.
    if x_slack_retry_num:
        logger.info("slack.bot ignoring retry #%s", x_slack_retry_num)
        return {"ok": True}

    event = payload.get("event") or {}
    # Never react to our own posts (or any bot's): the bot's reply is itself a
    # message event, so answering one would loop forever.
    if event.get("bot_id") or event.get("subtype"):
        return {"ok": True}
    if event.get("type") not in ("message", "app_mention"):
        return {"ok": True}

    background.add_task(_handle, event, payload.get("team_id") or "")
    return {"ok": True}
