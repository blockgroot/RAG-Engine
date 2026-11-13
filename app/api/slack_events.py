"""Ask Handbook from inside Slack (Events API receiver).

An adapter, not an agent: routing, the gate, the strict prompt and memory
are reused, so nothing here decides an answer.

- In a channel: answers from THAT channel only (`slack:channel:<id>` tag),
which everyone present can already read.
- In a DM: what the asker can read in the app (company and their spaces).

Identity: `team_id` is the connection's `external_workspace_id`; the Slack
user becomes an email (`users:read.email`), then a Handbook user. No account
means no answer.
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
from ..auth.users import get_member_by_address
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


#: No scope indexes the channel. A sentinel because ``None`` means org-wide.
_NO_SCOPE = object()


def _org_for_team(team_id: str) -> tuple[str, str | None] | None:
    """Resolve a Slack team id to ``(org_id, workspace_id)`` for the posting TOKEN.

    Which token posts is not what the answer may read. Org-wide first, for determinism.
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


_NO_DM_ACCESS = (
    "Slack is connected to Handbook only in spaces you're not in, so I can't answer "
    "you here. Ask in the Handbook web app, or ask your admin to connect Slack company-wide."
)


def _in_a_slack_space(org_id: str, team_id: str, user_id: str) -> bool:
    """With no company-wide Slack connection, a DM belongs only to the spaces that connected this Slack.

    ``_org_for_team`` returning a space token means there is no company connection.
    """
    with get_connection() as conn:
        return conn.execute(
            "SELECT 1 FROM oauth_connections c JOIN workspace_members m "
            "ON m.workspace_id = c.workspace_id AND m.user_id = %s::uuid "
            "WHERE c.org_id = %s::uuid AND c.provider = 'slack' "
            "AND c.external_workspace_id = %s AND NOT c.needs_reauth LIMIT 1",
            (user_id, org_id, team_id),
        ).fetchone() is not None


def _scope_for_channel(org_id: str, tag: str):
    """Which scope indexes this channel: ``None`` (org-wide), a space id, or ``_NO_SCOPE``.

    Asked of the documents, not the token: a private channel may live in one
    space while public ones are org-wide. Org-wide wins a tie (already
    company-readable).
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


def _to_slack_mrkdwn(text: str, *, keep_citations: bool = False) -> str:
    """Rewrite the model's Markdown as Slack mrkdwn.

    Escaped first (`&`, `<`, `>`, per Slack): model text can be steered by a
    document, and in mrkdwn `<url|text>` disguises a link and `<!channel>` pings
    the room.
    """
    # Citation markers point at nothing in Slack; an Act card keeps them (it shows
    # exactly what will be sent).
    if not keep_citations:
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
    """Org-wide plus every space this person is in, as ``(id, label)``.

    Read with `list_my_workspaces`, so a DM never reaches a space they are not in.
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
    """What `_answer` produced: the text and, maybe, a chart to upload.

    With a chart, ``text`` is the caption and ``with_values`` is the reply with
    the numbers written out, posted when the picture cannot be.
    """

    text: str
    chart: dict | None = None
    period: str = "month"
    with_values: str = ""


def _asks_for_chart(question: str) -> bool:
    """Does this channel question ask to SEE a chart, or how many of something?

    The same check Ask runs (``resolve.classify_route``). Never raises: a failed
    check is an ordinary question.
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
    """Run the existing pipeline; ``tags`` set means channel-scoped Slack only.

    ``scope_label`` ("Company", a space) prefixes the reply with where it
    looked; omitted in a channel. ``user_id`` is carried so an unanswered
    question is logged as a gap with a person (counted distinct).
    """
    watch_question(question)  # logged, never refused (guard/live.py)
    # A channel reply is read by the whole room, so it is built only from what
    # the scope can read, never as the asker. Decided here so no caller forgets.
    if scope_label is None:
        # ...plus that ONE channel's own documents: everyone reading the reply is in
        # it. Never the asker's other channels.
        viewer = Viewer(
            public_only=True,
            channels=(channel_id,) if channel_id else (),
        )

    chart_in_channel = bool(tags) and _asks_for_chart(question)
    if tags and not chart_in_channel:
        # Pinned to Slack and one channel, through the agent's pipeline: only this
        # caller needs a tag filter.
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
        # A chart of THIS channel only, as the room's viewer (public + this channel).
        decision = choose_agent(question, org_id, workspace_id=workspace_id,
                                viewer=viewer, chart_mode=True, channel_tag=tags[0])
    else:
        # Slack has no Chart toggle: a chart ask goes to Chart mode. The viewer
        # offers the asker's document tables and paragraph figures.
        decision = choose_agent(question, org_id, workspace_id=workspace_id,
                                viewer=viewer, chart_from_words=True)
        if other_scopes and _chart_refused(decision):
            # A DM tries the asker's other scopes in turn; the label names the one that answered.
            for scope_id, name in other_scopes[:MAX_DM_CHART_SCOPES]:
                other = choose_agent(question, org_id, workspace_id=scope_id,
                                     viewer=viewer, chart_mode=True)
                if getattr(other, "chart_spec", None) is not None:
                    decision, workspace_id, scope_label = other, scope_id, name
                    break
    from ..agent.orchestration import build_agent_graph
    from .chat import _agent_getters

    # Carry the chart spec as the web chat does: dropping it leaves InsightsAgent
    # with nothing and it refuses.
    spec = getattr(decision, "chart_spec", None)
    chart_spec = spec_to_dict(spec) if spec is not None else None

    graph_input = {
        "question": question,
        "org_id": org_id,
        "conversation_id": None,
        "workspace_id": workspace_id,
        "requested_agent": decision.agent_key,
        "stream": False,
        "chart_spec": chart_spec,
        "chart_refusal": getattr(decision, "chart_refusal", None),
        # Never the asker's real role: owner-only metrics stay off in Slack.
        "role": "member",
        # The asker's document access applies exactly as in the app.
        "viewer": viewer,
    }
    from ..insights.table_answers import reset_answers, use_answers

    figures = use_answers(getattr(decision, "table_answers", ()))
    try:
        response = build_agent_graph(_agent_getters()).invoke(graph_input)["response"]
    finally:
        reset_answers(figures)
    if not getattr(response, "grounded", True) and not getattr(response, "access_restricted", False):
        # The web chat's one retry of a refusal across every tool that cleared
        # the gate (`chat._refusal_retry`), so Slack answers what the app does.
        from ..graph import plan as graph_plan
        from .chat import _refusal_retry

        elsewhere = _refusal_retry(decision, None, org_id, workspace_id)
        if elsewhere is not None:
            decision, retry_plan, _tools = elsewhere
            token = graph_plan.use_plan(retry_plan)
            try:
                response = build_agent_graph(_agent_getters()).invoke(
                    {**graph_input, "requested_agent": decision.agent_key})["response"]
            finally:
                graph_plan.reset_plan(token)
    # Same gap log as the web chat. `getattr`: a missing field costs the gap log,
    # never the answer; absent counts as grounded.
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
    # Name the source and scope above the answer, so its origin can be checked.
    source = _SOURCE_LABELS.get(decision.agent_key, decision.agent_key)
    label = "" if scope_label is None else f"_{source} · {scope_label}_\n"
    with_values = label + _to_slack_mrkdwn(_with_chart_values(response))
    chart = getattr(response, "chart", None)
    if isinstance(chart, dict) and chart.get("points"):
        # A channel chart opens that channel's space; "this channel" means
        # nothing there, so the link's question names the channel.
        asked = question if scope_label is not None else _naming_channel(
            question, org_id, workspace_id, channel_id
        )
        link = _interactive_link(asked, workspace_id)
        # The picture carries the numbers; the text is the caption.
        return SlackAnswer(
            text=label + _to_slack_mrkdwn(response.answer) + link,
            chart=chart,
            period=getattr(response, "chart_period", None) or "month",
            with_values=with_values + link,
        )
    return SlackAnswer(with_values)


def _naming_channel(
    question: str, org_id: str, workspace_id: str | None, channel_id: str | None
) -> str:
    """The question with the channel it was asked in, for the web app, where
    the asker is no longer standing in it. Unchanged when the name is unknown."""
    from ..agent.slack_agent import _channel_names_for

    name = _channel_names_for(org_id, workspace_id).get(channel_id or "")
    return f"{question} (in #{name})" if name else question


def _interactive_link(question: str, workspace_id: str | None) -> str:
    """A link that re-asks this question in the web app's Chart mode.

    It carries the question, never the numbers, and runs as whoever clicks.
    Empty when the web address is not configured.
    """
    from urllib.parse import quote

    from ..config.settings import ApiSettings

    base = (ApiSettings.from_env().frontend_url or "").rstrip("/")
    if not base.startswith(("https://", "http://")):
        return ""
    path = f"/workspaces/{quote(workspace_id)}/ask" if workspace_id else "/chat"
    url = f"{base}{path}?mode=chart&q={quote(question[:500], safe='')}"
    return f"\n<{url}|Open interactive chart>"


def _count(value) -> str:
    """Render a metric value; counts are whole things, never "2.0"."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else f"{number:g}"


def _with_chart_values(response) -> str:
    """Append a chart's numbers to its caption as text.

    The fallback when the chart cannot be uploaded (`_upload_chart`).
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


#: Shown while the answer is built, then replaced by it. Names no source: that is not decided yet.
_SEARCHING = "_Searching your connected tools…_"


def _handle(event: dict, team_id: str) -> None:
    """Resolve who asked, answer, and post back. Runs AFTER Slack was acked."""
    channel = event.get("channel")
    slack_user = event.get("user")
    # The leading "<@U…>" addresses the bot and is not part of the question.
    text = re.sub(r"^(?:<@[A-Z0-9]+>\s*)+", "", (event.get("text") or "").strip())
    # Channel: always answer in a thread, so long answers do not bury the
    # channel. DM: answer inline, since a thread only adds a click. An existing
    # thread wins in both; anything not explicitly a DM threads.
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
    # By the person's current OR prior address: someone whose login changed is
    # still the same person, with the same access (`get_member_by_address`).
    user = get_member_by_address(email, org_id) if email else None
    if user is None:
        post_message(token, channel, _NO_ACCOUNT, thread_ts)
        return
    if is_dm and token_workspace_id is not None and not _in_a_slack_space(org_id, team_id, user.id):
        post_message(token, channel, _NO_DM_ACCESS, thread_ts)
        return

    # The same person and company limits as a question in the web app, so
    # Slack is not a way around them.
    from ..security.rate_limit import question_decision, refusal_message

    allowance = question_decision(user.id, org_id)
    if not allowance.allowed:
        post_message(token, channel, refusal_message(allowance.retry_after, allowance.refused_by), thread_ts)
        return

    # Act: a message that ASKS for an action gets a draft to approve (only when
    # the company allowed Act); anything else is answered.
    from .slack_act import handle_act, slack_request

    request = slack_request(text, org_id=org_id, is_dm=is_dm)
    if request is not None:
        handle_act(request=request, org_id=org_id, token=token, user=user,
                   slack_user=slack_user, channel=channel, thread_ts=thread_ts, is_dm=is_dm,
                   in_thread=bool(event.get("thread_ts")))
        return

    # A DM reads the asker's own scopes (`_dm_scopes`). A channel reads THAT
    # channel in whichever scope indexes it, so a private channel can stay in one
    # space. `channel_type == "im"` marks a DM.
    if is_dm:
        # Several scopes: the corpus picks the best match among the caller's own.
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
        # "Not connected" is a different fact from "nothing answers", and only it says
        # what to do. Checked before the pipeline, so it costs no model call.
        if resolved is _NO_SCOPE:
            post_message(token, channel, _NOT_CONNECTED, thread_ts)
            return
        answer_workspace_id, tags = resolved, [tag]
        # No label in a channel: the asker is standing in the only place the
        # answer could have come from, so naming it is noise.
        scope_label = None
        other_scopes = None

    # An answer can take 10-30s and Slack shows nothing meanwhile, so post a
    # placeholder first and edit the answer into it (`slack_utils.update_message`
    # explains why not `assistant.threads.setStatus`).
    placeholder_ts = post_message(token, channel, _SEARCHING, thread_ts)

    try:
        reply = _answer(
            text,
            org_id,
            answer_workspace_id,
            tags,
            scope_label,
            user_id=user.id,
            # Viewer from the resolved Handbook user, not the Slack profile, so both
            # surfaces agree on who is asking.
            viewer=viewer_for_person(org_id, user.email),
            # The room the reply lands in, so a channel answer may read that
            # channel's own threads without being anybody.
            channel_id=channel,
            other_scopes=other_scopes,
        )
    except Exception as exc:  # noqa: BLE001 - a failed answer must still reply
        logger.warning("slack.bot answer failed for org %s: %s", org_id, exc, exc_info=True)
        reply = SlackAnswer(_ERROR)

    # Edit the placeholder if any, else post; a failed edit still delivers the answer.
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
    """Draw the chart and upload it under the caption: ``"ok"``, ``"missing_scope"`` or ``"error"``.

    Built under the same access as the text, so it discloses nothing more.
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
    """The bot's own post or edit, or a question to it: never indexed, so no sync is flagged.

    Otherwise every answer flagged two or three syncs, each a slow Slack history read.
    """
    message = event.get("message") or event.get("previous_message") or event
    if event.get("bot_id") or message.get("bot_id"):
        return True
    text = message.get("text") or ""
    return any(f"<@{u}>" in text for u in bot_users)


def _is_channel_content(event: dict) -> bool:
    """A message posted, edited or deleted in a channel (not a DM, not a mention).

    Edit/delete events may omit `channel_type`, so the id decides: `C`/`G` channel, `D` DM.
    """
    if event.get("type") != "message":
        return False
    kind = event.get("channel_type")
    if kind in ("channel", "group"):
        return True
    return kind is None and str(event.get("channel") or "")[:1] in ("C", "G")


def _flag_channel_sync(team_id: str, channel: str) -> None:
    """Stamp `sync_requested_at` for connections indexing this channel; never raises."""
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
    """Receive one Slack event: ack at once, answer in the background.

    Slack retries after three seconds; inline work would answer several times.
    """
    return await _receive(
        request, background, x_slack_request_timestamp, x_slack_signature,
        x_slack_retry_num, SlackSettings.from_env().signing_secret,
    )


@router.post("/events/{app_key}")
async def slack_events_own_app(
    app_key: str,
    request: Request,
    background: BackgroundTasks,
    x_slack_request_timestamp: str | None = Header(default=None),
    x_slack_signature: str | None = Header(default=None),
    x_slack_retry_num: str | None = Header(default=None),
):
    """The same, from a company's own Slack app (``org_apps``).

    The key only picks the signing secret; the signature proves the app.
    """
    from ..auth.org_apps import org_for_app_key

    found = org_for_app_key("slack", app_key)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    org_id, secret = found
    return await _receive(
        request, background, x_slack_request_timestamp, x_slack_signature,
        x_slack_retry_num, secret, org_id=org_id,
    )


async def _receive(
    request: Request,
    background: BackgroundTasks,
    x_slack_request_timestamp: str | None,
    x_slack_signature: str | None,
    x_slack_retry_num: str | None,
    secret: str | None,
    *,
    org_id: str | None = None,
):
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

    # A company's app holds its own signing secret, so its requests are trusted
    # only for a Slack workspace connected to that company.
    if org_id is not None:
        scope = _org_for_team(payload.get("team_id") or "")
        if scope is None or scope[0] != org_id:
            return {"ok": True}

    event = payload.get("event") or {}

    # A channel message is a content change: flag a sync of the connections
    # indexing it and never answer (questions arrive as `app_mention`). Edits and
    # deletions count too. Before the retry drop: the flag is idempotent.
    if _is_channel_content(event):
        # `authorizations` names the bot user this delivery is for.
        bot_users = {a.get("user_id") for a in payload.get("authorizations") or [] if a.get("user_id")}
        if _is_bot_traffic(event, bot_users):
            return {"ok": True}
        background.add_task(
            _flag_channel_sync, payload.get("team_id") or "", event.get("channel") or ""
        )
        return {"ok": True}

    # Drop Slack retries: the first delivery is already answering (a cold start
    # misses the 3s ack), and honouring them posts duplicates.
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
