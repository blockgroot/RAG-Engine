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
import logging
import time

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from ..agent.routing import choose_agent
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


def _org_for_team(team_id: str) -> tuple[str, str | None] | None:
    """Resolve a Slack team id to ``(org_id, workspace_id)``.

    Prefers an org-wide connection over a space-scoped one when a team somehow
    has both: the bot has no space context in a DM, and answering from the
    narrower scope would silently hide content the asker can see in the app.
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


def _slack_email(token: str, user_id: str) -> str | None:
    from ..sources.slack_utils import _get  # local: same module, private helper

    try:
        info = _get(token, "users.info", {"user": user_id})
    except Exception as exc:  # noqa: BLE001 - an identity miss is not fatal
        logger.warning("slack.bot users.info failed for %s: %s", user_id, exc)
        return None
    return ((info.get("user") or {}).get("profile") or {}).get("email")


def _answer(question: str, org_id: str, workspace_id: str | None, tags: list[str] | None) -> str:
    """Run the existing pipeline. ``tags`` set => channel-scoped Slack only."""
    if tags:
        # Pinned to Slack and filtered to one channel. Goes through the agent's
        # pipeline rather than the routing graph because `Agent.answer` has no
        # tag argument -- and it must not gain one: a tag filter is meaningful
        # for exactly this caller, and every other agent would have to ignore it.
        result = get_slack_agent().pipeline.answer(
            question, org_id=org_id, workspace_id=workspace_id, tags=tags
        )
        return result.answer

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
    return _with_chart_values(response)


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
            lines.append(f"• {label}: {value}")
    if not lines:
        return response.answer
    more = "" if len(points) <= 10 else f"\n…and {len(points) - 10} more"
    return f"{response.answer}\n" + "\n".join(lines) + more


def _handle(event: dict, team_id: str) -> None:
    """Resolve who asked, answer, and post back. Runs AFTER Slack was acked."""
    channel = event.get("channel")
    slack_user = event.get("user")
    text = (event.get("text") or "").strip()
    # Reply inside the thread when the question was already in one, so a busy
    # channel does not get a second top-level message per question.
    thread_ts = event.get("thread_ts") or event.get("ts")
    if not channel or not slack_user or not text:
        return

    scope = _org_for_team(team_id)
    if scope is None:
        logger.info("slack.bot: no connection for team %s", team_id)
        return
    org_id, workspace_id = scope

    try:
        token = get_live_connection_token(org_id, "slack", workspace_id=workspace_id)
    except ProviderError as exc:
        logger.warning("slack.bot token unavailable for org %s: %s", org_id, exc)
        return

    email = _slack_email(token, slack_user)
    user = get_user_by_email(email) if email else None
    if user is None or user.org_id != org_id:
        post_message(token, channel, _NO_ACCOUNT, thread_ts)
        return

    # A channel question is answered from that channel ONLY; a DM searches
    # everything the asker could see in the app. `channel_type == "im"` is how
    # Slack marks a direct message.
    tags = None if event.get("channel_type") == "im" else [channel_tag(channel)]

    try:
        answer = _answer(text, org_id, workspace_id, tags)
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

    event = payload.get("event") or {}
    # Never react to our own posts (or any bot's): the bot's reply is itself a
    # message event, so answering one would loop forever.
    if event.get("bot_id") or event.get("subtype"):
        return {"ok": True}
    if event.get("type") not in ("message", "app_mention"):
        return {"ok": True}

    background.add_task(_handle, event, payload.get("team_id") or "")
    return {"ok": True}
