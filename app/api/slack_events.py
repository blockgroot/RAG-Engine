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
from dataclasses import dataclass

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from ..agent.routing import _NO_MATCH, NOT_CHART_GAPS, choose_agent, choose_scope
from ..feedback import record_gap
from ..guard.live import watch_question
from ..rag.cite import strip_citations
from ..auth.credentials import get_live_connection_token
from ..auth.users import get_user_by_email
from ..sources.google_groups import viewer_for_person
from ..jobs.autosync import request_sync_external
from ..vectorstore.base import Viewer
from ..chartrender import build_chart_renderer
from ..config.settings import SlackSettings
from ..core.exceptions import ProviderError
from ..insights.resolve import spec_to_dict
from ..db.connection import get_connection
from ..sources.slack_utils import channel_tag, post_message, update_message, upload_file
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

    Escaped FIRST, per Slack's own rule (`&` then `<` then `>`): in mrkdwn
    `<https://evil?d=…|click here>` is a disguised link and `<!channel>` pings
    the room, and the model's text is steerable by any document it read — the
    Slack AI exfiltration (PromptArmor, 2024) was exactly a model-written
    `<url|text>`. Escaped, both arrive as inert text.
    """
    # Inline citation markers are for the web chat's chips; in Slack a bare
    # "[2]" points at nothing the reader can open.
    text = strip_citations(text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
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


@dataclass(frozen=True)
class SlackAnswer:
    """What `_answer` produced: the text to post and, maybe, a chart to upload.

    ``chart`` is set only when there is a chart to draw; then ``text`` is the
    caption alone and ``with_values`` is the same reply with the numbers
    written out, posted instead when the picture cannot be.
    """

    text: str
    chart: dict | None = None
    period: str = "month"
    with_values: str = ""


def _asks_for_chart(question: str) -> bool:
    """Does this channel mention ask to SEE a chart, or how many of something?

    The same question check Ask runs (``resolve.classify_route``), so a chart
    ask in a channel reaches Chart mode instead of the Slack-only pipeline,
    which cannot chart. Never raises: a failed check is an ordinary question.
    """
    try:
        from ..insights.resolve import classify_route

        return classify_route(question, github=False).chart_ask in ("visual", "count")
    except Exception:  # noqa: BLE001
        logger.warning("slack: chart question check failed", exc_info=True)
        return False


#: Other spaces a DM chart is tried in when the probed one has none. Each try
#: is a classifier call (and maybe a paragraph read), so it is bounded.
MAX_DM_CHART_SCOPES = 3


def _chart_refused(decision) -> bool:
    """Chart mode answered with a refusal that another scope could change.

    Not a model that cannot chart, and not a question that wanted words."""
    return (
        getattr(decision, "agent_key", None) == "insights"
        and getattr(decision, "chart_spec", None) is None
        and getattr(decision, "reason", None) == "chart-refuse"
    )


def _answer(
    question: str,
    org_id: str,
    workspace_id: str | None,
    tags: list[str] | None,
    scope_label: str | None = None,
    user_id: str | None = None,
    viewer: Viewer | None = None,
    channel_id: str | None = None,
    other_scopes: list[tuple[str | None, str]] | None = None,
) -> SlackAnswer:
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
    watch_question(question)  # logged, never refused (guard/live.py)
    # A CHANNEL reply is read by everyone in the room, so it may only ever be
    # built from documents the whole scope can read. Answering a channel as the
    # ASKER would publish their private documents to every other member --
    # the widest hole this feature could have, and one the asker never chose.
    # `scope_label is None` is exactly the channel case (a DM always names its
    # scope), and it is decided HERE rather than at the call site so a future
    # caller cannot forget it.
    if scope_label is None:
        # ...but it MAY read that channel's own documents. Everyone who can see
        # this reply is already a member of the channel it is posted in, so
        # quoting it discloses nothing they cannot scroll up and read -- while
        # without this the bot goes silent in a private channel about the very
        # threads it is sitting in, which is the whole reason anyone connected
        # it. Exactly ONE channel, the one being replied in: a viewer carrying
        # the asker's whole channel list could carry a private channel's
        # content into a different room.
        viewer = Viewer(
            public_only=True,
            channels=(channel_id,) if channel_id else (),
        )

    chart_in_channel = bool(tags) and _asks_for_chart(question)
    if tags and not chart_in_channel:
        # Pinned to Slack and filtered to one channel. Goes through the agent's
        # pipeline rather than the routing graph because `Agent.answer` has no
        # tag argument -- and it must not gain one: a tag filter is meaningful
        # for exactly this caller, and every other agent would have to ignore it.
        result = get_slack_agent().pipeline.answer(
            question,
            org_id=org_id,
            workspace_id=workspace_id,
            tags=tags,
            viewer=viewer,
        )
        if not result.answered and not result.access_restricted:
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
        return SlackAnswer(_to_slack_mrkdwn(result.answer))

    if chart_in_channel:
        # A chart of THIS channel only: its conversations and the figures
        # posted in it, as the room's viewer (public + this channel). Never
        # Drive, Notion or another channel -- the reply is read by the room.
        decision = choose_agent(question, org_id, workspace_id=workspace_id,
                                viewer=viewer, chart_mode=True, channel_tag=tags[0])
    else:
        # Slack has no Chart toggle: a question the question check reads as a
        # chart ask is answered in Chart mode; everything else is Ask. The
        # viewer is what offers the asker's document tables and paragraph
        # figures -- without it Chart mode offers none.
        decision = choose_agent(question, org_id, workspace_id=workspace_id,
                                viewer=viewer, chart_from_words=True)
        if other_scopes and _chart_refused(decision):
            # A DM reaches everything the asker can: company-wide and each of
            # their spaces. The probed scope had no chart for this, so the
            # others are asked in turn, and the first chart answers -- the
            # label names the space it came from.
            for scope_id, name in other_scopes[:MAX_DM_CHART_SCOPES]:
                other = choose_agent(question, org_id, workspace_id=scope_id,
                                     viewer=viewer, chart_mode=True)
                if getattr(other, "chart_spec", None) is not None:
                    decision, workspace_id, scope_label = other, scope_id, name
                    break
    from ..agent.orchestration import build_agent_graph
    from .chat import _agent_getters

    # The chart spec has to be carried through exactly as the web chat does.
    # `choose_agent` can route to InsightsAgent AND resolve the spec in one
    # step, so dropping the spec sends a routed chart question to an agent
    # holding nothing -- which answers "I can't chart that", a flat denial of
    # something the router had already resolved.
    spec = getattr(decision, "chart_spec", None)
    chart_spec = spec_to_dict(spec) if spec is not None else None

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
            # The asker was already resolved to a Handbook account above (we
            # refuse outright without one), so document-level access applies
            # here exactly as it does in the app -- a bot that answered from
            # documents the asker cannot open would be the widest hole of all,
            # since a channel reply is read by everyone in the room.
            "viewer": viewer,
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
    if not getattr(response, "grounded", True) and not getattr(
        response, "access_restricted", False
    ) and getattr(decision, "reason", None) not in NOT_CHART_GAPS:
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
    # WHICH source and WHICH scope, above the answer. With one box answering
    # from a company's Notion and from several private spaces, "where did this
    # come from?" is not answerable from the text -- and an answer whose origin
    # cannot be checked is the failure this whole codebase is arranged against.
    source = _SOURCE_LABELS.get(decision.agent_key, decision.agent_key)
    label = "" if scope_label is None else f"_{source} · {scope_label}_\n"
    with_values = label + _to_slack_mrkdwn(_with_chart_values(response))
    chart = getattr(response, "chart", None)
    if isinstance(chart, dict) and chart.get("points"):
        # A channel chart reads that one channel, which the web app cannot
        # scope to, so the link is offered in a DM only.
        link = _interactive_link(question, workspace_id) if scope_label is not None else ""
        # The picture carries the numbers; the text is the caption.
        return SlackAnswer(
            text=label + _to_slack_mrkdwn(response.answer) + link,
            chart=chart,
            period=getattr(response, "chart_period", None) or "month",
            with_values=with_values + link,
        )
    return SlackAnswer(with_values)


def _interactive_link(question: str, workspace_id: str | None) -> str:
    """A Slack link that opens this question in the web app's Chart mode.

    Slack messages cannot run a chart (no scripts, no embeds), so the image is
    a picture of it. The link re-asks in the web app as whoever clicks, under
    THEIR access -- it carries the question, never the chart's numbers, so it
    shows nobody more than they could ask for themselves. Empty when the web
    app's address is not configured."""
    from urllib.parse import quote

    from ..config.settings import ApiSettings

    base = (ApiSettings.from_env().frontend_url or "").rstrip("/")
    if not base.startswith(("https://", "http://")):
        return ""
    path = f"/workspaces/{quote(workspace_id)}/ask" if workspace_id else "/chat"
    url = f"{base}{path}?mode=chart&q={quote(question[:500], safe='')}"
    return f"\n<{url}|Open interactive chart>"


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
    question -- so the buckets are printed. With `files:write` the chart is
    uploaded as a picture instead (`_upload_chart`); these lines are what a
    reply falls back to when it cannot be.
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


#: Shown while the answer is being built, then REPLACED by it. Italic because
#: it is the bot talking about itself rather than answering; "your connected
#: tools" rather than naming one, because which source answers is decided
#: inside `_answer` and has not been measured yet at this point.
_SEARCHING = "_Searching your connected tools…_"


def _handle(event: dict, team_id: str) -> None:
    """Resolve who asked, answer, and post back. Runs AFTER Slack was acked."""
    channel = event.get("channel")
    slack_user = event.get("user")
    text = (event.get("text") or "").strip()
    # ALWAYS answer in a thread: an existing one when the question was already
    # inside it, otherwise a new thread hanging off the question itself.
    #
    # This reverses an earlier call to reply in-channel for top-level
    # questions, whose reasoning was that a "1 reply" link is a second place to
    # look for one answer. In a real CHANNEL the opposite cost dominates: a
    # grounded answer is long, and posting several of them straight into the
    # channel buries whatever else people were discussing. Threading keeps the
    # answer attached to the question that earned it and leaves the channel
    # readable.
    #
    # A DM is the opposite case and now gets the opposite rule. There is
    # nothing to bury -- the conversation IS the bot -- so a thread only adds a
    # "1 reply" link to click before you can read your own answer, which is
    # exactly the cost the original in-channel reasoning named. The earlier
    # "one rule for both" note traded that away for having no second code
    # path; the branch is one line, and a DM that reads like a chat is worth
    # more than the line it costs.
    #
    # An existing thread still wins in BOTH surfaces: if the question was asked
    # inside a thread, answering outside it drops the answer somewhere the
    # asker is not looking. `channel_type` is absent on a channel
    # `app_mention`, so anything that is not explicitly a DM keeps threading.
    is_dm = event.get("channel_type") == "im"
    thread_ts = event.get("thread_ts") if is_dm else (
        event.get("thread_ts") or event.get("ts")
    )
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
    # `channel_type == "im"` is how Slack marks a direct message (resolved once,
    # above, because the reply's SHAPE depends on it too).
    if is_dm:
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
        other_scopes = None
        if len(scopes) > 1:
            # The counterpart to `choose_agent`, and the same router module --
            # a DM is the one surface with no UI to say which space it means.
            best = choose_scope(org_id, scopes, text)
            if best is not _NO_MATCH:
                answer_workspace_id = best
                scope_label = next(
                    (name for sid, name in scopes if sid == best), "Company"
                )
            other_scopes = [(sid, name) for sid, name in scopes if sid != answer_workspace_id]
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
        other_scopes = None

    # A grounded answer is ~6 LLM calls and routinely takes 10-30 seconds. Slack
    # shows nothing at all in that window, so the bot reads as broken or
    # ignored -- people re-ask, which costs another six calls. A placeholder
    # posted FIRST and then edited in place is the whole indicator: the answer
    # lands in the message the asker is already watching, rather than arriving
    # underneath a "thinking..." line that stays there forever.
    #
    # Slack's own status API (`assistant.threads.setStatus`) is deliberately
    # not used -- see `slack_utils.update_message` for why it cannot apply to
    # a bot that answers in channels and plain DMs.
    placeholder_ts = post_message(token, channel, _SEARCHING, thread_ts)

    try:
        reply = _answer(
            text,
            org_id,
            answer_workspace_id,
            tags,
            scope_label,
            user_id=user.id,
            # `user` is the Handbook account this Slack identity resolved to,
            # so their document access here is the same as in the app. Built
            # from the resolved row, not from the Slack profile email, so the
            # two surfaces can never disagree about who is asking.
            viewer=viewer_for_person(org_id, user.email),
            # The room the reply lands in, so a channel answer may read that
            # channel's own threads without being anybody.
            channel_id=channel,
            other_scopes=other_scopes,
        )
    except Exception as exc:  # noqa: BLE001 - a failed answer must still reply
        logger.warning("slack.bot answer failed for org %s: %s", org_id, exc, exc_info=True)
        reply = SlackAnswer(_ERROR)

    # Edit the placeholder when we have one, post fresh when we do not. A
    # failed edit must still deliver the answer: leaving "Searching..." in
    # place and dropping a grounded reply is the worst of both.
    reply_ts = _deliver(token, channel, placeholder_ts, reply.text, thread_ts)
    if reply.chart is None:
        return
    status = _upload_chart(token, channel, thread_ts, reply)
    if status == "ok":
        return
    # No picture: the numbers go back into the text, so the caption is never
    # a title with nothing under it.
    fallback = reply.with_values
    if status == "missing_scope":
        fallback += f"\n{_RECONNECT_FOR_IMAGES}"
    _deliver(token, channel, reply_ts, fallback, thread_ts)


#: Shown under the text values when the connection lacks `files:write`.
_RECONNECT_FOR_IMAGES = "_An admin can reconnect Slack to get chart images._"


def _deliver(
    token: str, channel: str, ts: str | None, text: str, thread_ts: str | None
) -> str | None:
    """Edit message ``ts`` into ``text``, or post it fresh. Returns the message ts."""
    if ts and update_message(token, channel, ts, text):
        return ts
    return post_message(token, channel, text, thread_ts)


def _upload_chart(token: str, channel: str, thread_ts: str | None, reply: SlackAnswer) -> str:
    """Draw the chart and upload it under the caption. ``"ok"`` | ``"missing_scope"`` | ``"error"``.

    The panel was built for this asker under the same access rules as the
    text (a channel reply is public-only plus that channel), so the picture
    discloses nothing the caption's numbers would not.
    """
    panel = reply.chart or {}
    try:
        png = build_chart_renderer().render_png(panel, period=reply.period)
    except ProviderError as exc:
        logger.warning("slack.bot chart render failed: %s", exc)
        return "error"
    title = panel.get("title") or "Chart"
    return upload_file(
        token, channel, png,
        filename="chart.png",
        title=title,
        alt_text=panel.get("explain") or title,
        # The caption is already posted as the message above the image.
        initial_comment=None,
        thread_ts=thread_ts,
    )


def _is_bot_traffic(event: dict, bot_users: set[str]) -> bool:
    """The bot's own post/edit, or a question TO it -- ingest never indexes
    either (`sources.slack._is_bot_traffic`), so a sync for one finds nothing.
    Without this every answer flagged a sync twice (placeholder, then its edit)
    and every question a third time, each a Slack history read at 1/min."""
    message = event.get("message") or event.get("previous_message") or event
    if event.get("bot_id") or message.get("bot_id"):
        return True
    text = message.get("text") or ""
    return any(f"<@{u}>" in text for u in bot_users)


def _is_channel_content(event: dict) -> bool:
    """A message posted, edited or deleted in a channel (not a DM, not a mention).

    `channel_type` is "channel"/"group" on a posted message, but Slack's own
    examples for `message_changed`/`message_deleted` omit it, so an edit falls
    back to the channel id: `C`/`G` are channels, `D` is a DM.
    """
    if event.get("type") != "message":
        return False
    kind = event.get("channel_type")
    if kind in ("channel", "group"):
        return True
    return kind is None and str(event.get("channel") or "")[:1] in ("C", "G")


def _flag_channel_sync(team_id: str, channel: str) -> None:
    """Stamp `sync_requested_at` for connections indexing this channel.

    Never raises: a missed flag costs one poll interval, and the ack has
    already gone out.
    """
    if not team_id or not channel:
        return
    try:
        request_sync_external("slack", team_id, slack_channel=channel)
    except Exception:  # noqa: BLE001
        logger.warning("slack.events: could not flag a sync for %s", channel, exc_info=True)


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

    event = payload.get("event") or {}

    # A message in a CHANNEL (public `message.channels`, private
    # `message.groups`) is content changing, not a question: flag a sync of the
    # connections that index that channel and never answer it -- answering
    # every channel message would make the bot reply to all conversation. The
    # bot's questions arrive as `app_mention`, which is handled below. Edits
    # and deletions (`message_changed` / `message_deleted` subtypes) are
    # content changes too, so the subtype filter does not apply here. Done
    # BEFORE the retry drop: the flag is idempotent, so a retry of a delivery
    # that died on a cold start still lands.
    if _is_channel_content(event):
        # `authorizations` names the bot user this delivery is for.
        bot_users = {a.get("user_id") for a in payload.get("authorizations") or [] if a.get("user_id")}
        if _is_bot_traffic(event, bot_users):
            return {"ok": True}
        background.add_task(
            _flag_channel_sync, payload.get("team_id") or "", event.get("channel") or ""
        )
        return {"ok": True}

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

    # Never react to our own posts (or any bot's): the bot's reply is itself a
    # message event, so answering one would loop forever.
    if event.get("bot_id") or event.get("subtype"):
        return {"ok": True}
    if event.get("type") not in ("message", "app_mention"):
        return {"ok": True}

    background.add_task(_handle, event, payload.get("team_id") or "")
    return {"ok": True}
