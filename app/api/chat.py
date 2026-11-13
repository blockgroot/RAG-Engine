"""Chat routes for SSE answers, scoped conversations, and starter prompts."""

from __future__ import annotations

import asyncio
import contextvars

import json
import logging
import os
import dataclasses
import queue
import re
import threading
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from collections.abc import AsyncIterator, Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool

def _stream_word_delay_seconds() -> float:
    raw = os.getenv("CHAT_STREAM_WORD_DELAY_MS")
    if raw is None or raw.strip() == "":
        return 0.05
    try:
        return max(0.0, float(raw) / 1000.0)
    except ValueError:
        return 0.05


def _word_chunks(text: str) -> Iterator[str]:
    """Yield one word at a time (trailing whitespace stays with the word)."""
    if not text:
        return
    i = 0
    n = len(text)
    while i < n:
        j = i
        while j < n and not text[j].isspace():
            j += 1
        while j < n and text[j].isspace():
            j += 1
        if j == i:
            yield text[i:]
            return
        yield text[i:j]
        i = j

from ..agent.github_agent import GitHubAgent
from ..agent.orchestration import build_agent_graph, route_agent_key
from ..agent.routing import (
    _ROUTING_POOL,
    EMBEDDED_PROVIDERS,
    NOT_CHART_GAPS,
    _connected_providers,
    choose_agent,
    named_providers,
    refusal_retry,
)
from ..agent.rag_pipeline_agent import RagPipelineAgent
from ..config.settings import AttachmentSettings, GraphSettings, RagSettings, RateLimitSettings
from ..rag import progress
from ..rag.deep import reset_deep_read, use_deep_read
from ..core.exceptions import AuthError, LLMProviderError, ProviderError
from ..graph import plan as graph_plan
from ..livetools import LiveRequest, current_live_request, reset_live_request, use_live_request
from ..memory import personal as personal_memory
from ..memory import conversations as conversation_store
from ..llm import catalog
from ..llm import org_model
from ..llm.routed import answering_model, selected_model, use_model
from ..security.ai_spend import charge_to
from ..db.connection import get_connection
from ..feedback import record_gap
from ..guard.live import ATTACHMENT_WARNING, is_flagged, watch_question
from ..agent.orchestration import INSIGHTS_KEY
from ..insights import query
from ..insights.resolve import spec_to_dict
from ..security.rate_limit import give_back_place, limit_question, take_place
from ..security.visibility import visibility_predicate
from ..workspaces import assert_member
from .deps import (
    SessionClaims,
    get_drive_agent,
    get_github_agent,
    get_insights_agent,
    get_linear_agent,
    get_notion_agent,
    get_policy_agent,
    get_session,
    get_slack_agent,
    get_workspace_agent,
    viewer_for,
)

from .suggestions import (
    build_combined_suggestions,
    build_forms_suggestions,
    build_github_suggestions,
    build_linear_suggestions,
    build_policy_suggestions,
    build_slack_suggestions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


def _is_own_model(org_id: str, model: str | None) -> bool:
    """True only for THIS org's configured model id."""
    if not model:
        return False
    own = org_model.get_org_model_summary(org_id)
    return bool(own and own["model"] == model)


@router.get("/models")
def list_models(session: SessionClaims = Depends(get_session)):
    """The models a member may pick, plus the default's identity.

    Session-gated: no free reconnaissance of the deployment's models. ``models``
    is empty when no selectable backend is configured, which hides the picker.
    """
    # Only models whose backend has credentials: otherwise the default would
    # answer under another model's label.
    from ..llm import build_llm_provider

    provider = build_llm_provider()
    backends = provider.configured_backends()
    # The default is labelled with the real model (from LLM_MODEL), not "Auto";
    # its VALUE stays ``catalog.AUTO``.
    from ..config.settings import LLMSettings

    default_model = LLMSettings.from_env().model
    # The default is already the first option; listing it again under its
    # catalog label would offer one model twice.
    models = [m for m in catalog.as_dicts(backends) if m["id"] != default_model]

    # The org's own model, added beside the built-ins so members have a way out
    # when its key breaks.
    own = org_model.get_org_model_summary(session.org_id)
    if own:
        models.append(
            {
                # Company first: `note` is only a hover title, and touch has no hover.
                "id": own["model"],
                "label": f"Your company's model — {own['model']}",
                "note": f"Configured by your admin ({own.get('preset_label') or 'custom'}).",
                "backend": "custom",
                # Not ours to judge: offered in Chart mode with a note.
                "charts": None,
            }
        )

    return {
        "default": catalog.AUTO,
        "default_label": getattr(catalog.get(default_model or ""), "label", None)
        or default_model or "Auto",
        # The default model builds charts; Chart mode always offers it.
        "default_charts": True,
        "models": models,
    }

AGENT_GITHUB = "github"
AGENT_POLICY = "policy"
AGENT_SLACK = "slack"
AGENT_LINEAR = "linear"
AGENT_NOTION = "notion"
AGENT_GOOGLE = "google"

MAX_QUESTION_CHARS = 4000


def _select_agent(
    workspace_id: str | None,
    requested_agent: str | None = None,
) -> RagPipelineAgent | GitHubAgent:
    """Resolve the concrete agent object for conversation creation."""
    key = route_agent_key(workspace_id, requested_agent)
    return _agent_getters()[key]()


def _agent_getters() -> dict[str, RagPipelineAgent | GitHubAgent]:
    """Build the shared routing table lazily so tests can monkeypatch getters."""
    return {
        AGENT_GITHUB: get_github_agent,
        AGENT_SLACK: get_slack_agent,
        AGENT_LINEAR: get_linear_agent,
        AGENT_NOTION: get_notion_agent,
        AGENT_GOOGLE: get_drive_agent,
        "insights": get_insights_agent,
        "workspace": get_workspace_agent,
        AGENT_POLICY: get_policy_agent,
    }


def _agent_graph():
    """Build the routing graph fresh so tests see monkeypatched getters."""
    return build_agent_graph(_agent_getters())


def _conversation_belongs_to_scope(
    conversation_id: str, org_id: str, workspace_id: str | None, user_id: str
) -> bool:
    """A client-supplied conversation id must match org, workspace AND owner.

    The owner check makes a conversation personal. A NULL ``user_id`` (rows from
    before the column) stays resumable in scope: no owner can be invented.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = %s AND org_id = %s "
            "AND workspace_id IS NOT DISTINCT FROM %s "
            "AND (user_id IS NULL OR user_id = %s)",
            (conversation_id, org_id, workspace_id, user_id),
        ).fetchone()
    return row is not None


def _user_facing_llm_error(exc: BaseException) -> str:
    """Map provider failures to a short, non-technical chat message."""
    text = str(exc).lower()
    cause = getattr(exc, "cause", None)
    if cause is not None:
        text = f"{text} {cause}".lower()
    if "429" in text or "rate limit" in text or "exhausted" in text:
        return (
            "I'm getting a lot of requests right now and couldn't finish that "
            "answer. Please wait a moment and try again."
        )
    if "timeout" in text:
        return "That took too long to answer. Please try again."
    return "I couldn't reach the answer service just now. Please try again shortly."


def _may_chart_sentiment(
    org_id: str, workspace_id: str | None, session: SessionClaims
) -> bool:
    """Whether to offer survey-sentiment chips.

    Needs Forms reading on, surveys selected in this scope (an allow-list), and
    `may_see_metric`, so a chip never leads to a revealing refusal.
    """
    try:
        from ..auth import get_connection_config
        from ..config.settings import GoogleSettings
        from ..insights import registry
        from ..insights.scopes import may_see_metric

        if not GoogleSettings.from_env().forms_enabled:
            return False
        config = get_connection_config(org_id, "google", workspace_id=workspace_id) or {}
        if not (config.get("form_ids") or []):
            return False
        return may_see_metric(
            registry.get("sentiment_by_theme"),
            role=session.role,
            workspace_id=workspace_id,
            org_id=org_id,
            user_id=session.user_id,
        )
    except Exception:  # noqa: BLE001 - chips are a convenience, never a 500
        logger.debug("Suggestions: sentiment gate check failed", exc_info=True)
        return False


def _combined_suggestions(
    org_id: str, workspace_id: str | None, session: SessionClaims
) -> dict:
    """Chips from every source connected in this scope, via each provider's builder.

    Each lookup is wrapped: one unreachable source costs only its own chips.
    """
    acl = viewer_for(session).acl()
    per_provider: dict[str, list[str]] = {}
    # Deep mode's starters, built by the same builders from the same lookup
    # (each source is read once), worded for reading whole documents.
    per_provider_deep: dict[str, list[str]] = {}

    def _try(key: str, fetch, build) -> None:
        try:
            found = fetch()
            questions = build(found, False)
            deep = build(found, True)
        except Exception:  # noqa: BLE001 - chips are a convenience
            logger.debug("Suggestions: %s lookup failed", key, exc_info=True)
            return
        if questions:
            per_provider[key] = questions
        if deep:
            per_provider_deep[key] = deep

    in_space = workspace_id is not None

    def _docs(titles, deep):
        return build_policy_suggestions(titles, workspace=in_space, deep=deep)

    _try("notion", lambda: _titles_for_scope_by_provider(org_id, workspace_id, "notion", acl), _docs)
    _try("google", lambda: _titles_for_scope_by_provider(org_id, workspace_id, "google", acl), _docs)
    _try(
        "slack",
        lambda: _slack_channel_names_for_scope(org_id, workspace_id),
        lambda names, deep: build_slack_suggestions(names, deep=deep),
    )
    _try(
        "linear",
        lambda: _linear_titles_for_scope(org_id, workspace_id, acl),
        lambda titles, deep: build_linear_suggestions(titles, deep=deep),
    )
    _try(
        "github",
        lambda: _github_repos_for_scope(org_id, workspace_id),
        lambda repos, deep: build_github_suggestions(repos, deep=deep),
    )

    if not per_provider.keys() & {"notion", "google"}:
        # Provider-agnostic titles only when no document provider resolved (rows with
        # no source_provider); otherwise they would duplicate the per-provider chips.
        _try("policy", lambda: _document_titles_for_scope(org_id, workspace_id, acl), _docs)

    # Survey sentiment only for someone allowed to see it. Not a Deep starter: it is a chart.
    if _may_chart_sentiment(org_id, workspace_id, session):
        _try("forms", lambda: None, lambda _, deep: [] if deep else build_forms_suggestions())

    questions = build_combined_suggestions(per_provider, workspace=in_space)
    deep_questions = build_combined_suggestions(per_provider_deep, workspace=in_space)
    # Which connector each chip came from, so the empty state can show its logo.
    # Decoration only: a chip with no entry simply shows no logo.
    source_of = {
        q: key
        for built_by in (per_provider, per_provider_deep)
        for key, built in built_by.items()
        for q in built
    }

    # No single agent produced these, and saying "policy" would be a lie the
    # client might act on.
    return {
        "agent": None,
        "sources": sorted(per_provider),
        "questions": questions,
        "deep_questions": deep_questions,
        "question_sources": {
            q: source_of[q] for q in [*questions, *deep_questions] if q in source_of
        },
    }


#: Chart-mode starters shown at once: two columns of three on a wide screen.
MAX_CHART_STARTERS = 6

#: Fewest starters shown when the scope has anything to chart.
MIN_CHART_STARTERS = 3

#: Order sources take turns in, documents first: their tables are the data a
#: person is least likely to know can be charted.
_STARTER_SOURCES = ("notion", "google", "slack", "linear", "github", "forms")


def _starter_label(label: str) -> str:
    """A metric label mid-sentence: "Where the work sits" -> "where the work
    sits", while "PRs" or "GitHub" keep their capitals."""
    return label[:1].lower() + label[1:] if label[1:2].islower() else label


#: How each shape is asked for. Every starter names its chart type, so the
#: list shows what Chart mode can draw; "donut" is never offered (a pie).
_SHAPE_WORDS = {
    "line": "Line chart", "area": "Area chart", "bar": "Bar chart",
    "horizontal_bar": "Horizontal bar chart", "stacked_bar": "Stacked bar chart",
    "stacked_area": "Stacked area chart", "heatmap": "Heatmap", "pie": "Pie chart",
}


#: Which chart types the list shows first, so six starters cover the common
#: ones before the rarer stacked area and heatmap.
_SHOWCASE = ("bar", "line", "pie", "stacked_bar", "area", "horizontal_bar",
             "heatmap", "stacked_area")


def _metric_forms(metric, attrs) -> list[tuple[str, str]]:
    """Every ``(shape, question)`` this metric can be drawn as, natural shape first.

    Built from its label, dimensions and discovered fields; shapes come from
    ``registry.shapes_for``. A median is never summed: no pie or stack.
    """
    from ..rag.prompts import _TOOL_NAMES

    tool = _TOOL_NAMES.get(metric.provider, metric.provider.title())
    label = _starter_label(metric.label)
    if metric.chart not in _SHAPE_WORDS:
        return [(metric.chart, f"Chart {label} in {tool}")]
    dims = [query.dim_label(metric, d) for d in metric.dims]
    by = attrs[0].label if attrs else (dims[0] if dims else None)
    adds = metric.select.lower().startswith("count")
    trend = [
        ("line", f"per week in {tool} over the last 3 months"),
        ("area", f"per month in {tool}"),
    ]
    split = [
        ("stacked_bar", f"per month in {tool}, split by {by}"),
        ("stacked_area", f"per week in {tool}, split by {by}"),
        ("heatmap", f"per month by {by} in {tool}"),
    ] if by and adds else []
    breakdown = [
        ("bar", f"by {by} in {tool}"),
        ("horizontal_bar", f"by {by} in {tool}"),
    ] + ([("pie", f"by {by} in {tool}")] if adds else []) if by else []
    if metric.chart in ("line", "area"):
        forms = trend + split + breakdown
    elif metric.chart == "stacked_bar":
        forms = split + breakdown + trend
    else:
        forms = breakdown + trend + split
    if metric.dims and metric.dims[0] == "state":
        # Where work sits NOW: a breakdown, never a trend; its stack is by a
        # second real column, split by state.
        from ..insights import registry

        forms = [f for f in breakdown]
        other = next((query.dim_label(metric, d) for d in metric.dims[1:]
                      if d in registry.DIMENSIONS), None)
        if other and adds:
            forms.append(("stacked_bar", f"by {other} in {tool}, split by {dims[0]}"))
    return [(shape, f"{_SHAPE_WORDS[shape]} of {label} {rest}") for shape, rest in forms]


def _table_forms(table) -> list[tuple[str, str]]:
    """A document table asked for by its column names and page title.

    Empty with nothing to plot; a pie only for a few rows of a summable measure.
    """
    if table.row_count < 2 or not table.columns:
        return []
    axis = table.columns[0]
    number = next((c for c in table.columns[1:] if c.get("type") == "number"), None)
    if not number or not axis.get("name"):
        return []
    measure = _starter_label(
        re.sub(r"\s*\([^)]*\)\s*$", "", number["name"])[:_STARTER_NAME_CHARS])
    axis_name = _starter_label(axis["name"][:_STARTER_NAME_CHARS])
    title = table.document_title
    if len(title) > 50:
        title = title[:50].rsplit(" ", 1)[0] + "…"
    source = f"from \u201c{title}\u201d"
    if axis.get("type") == "date":
        forms = [("line", f"over time {source}"), ("area", f"over time {source}"),
                 ("bar", f"over time {source}")]
    else:
        forms = [("bar", f"by {axis_name} {source}"),
                 ("horizontal_bar", f"by {axis_name} {source}")]
        if table.row_count >= 3:
            forms.append(("line", f"by {axis_name} {source}"))
        if table.row_count <= 8 and "%" not in number["name"] and number.get("unit") != "%":
            forms.append(("pie", f"by {axis_name} {source}"))
        second = next((c for c in table.columns[1:] if c.get("type") == "category"
                       and c.get("name")), None)
        if second:
            split = _starter_label(second["name"][:_STARTER_NAME_CHARS])
            forms.insert(1, ("stacked_bar", f"by {axis_name}, split by {split} {source}"))
    return [(shape, f"{_SHAPE_WORDS[shape]} of {measure} {rest}") for shape, rest in forms]


def chart_starters(metrics, fields: dict | None, tables=(), recorded=None) -> list[str]:
    """Starter questions built from what THIS scope can chart.

    Metrics with recorded facts (``None`` = keep all) and document tables,
    phrased from their own labels and columns, never copy. Sources take turns,
    and each starter uses a chart type no earlier one did.
    """
    by_provider: dict[str, list] = {}
    for table in tables:
        forms = _table_forms(table)
        items = by_provider.setdefault(table.provider, [])
        if forms and forms not in items and len(items) < 2:
            items.append(forms)
    for metric in metrics:
        if recorded is not None and (metric.provider, metric.kind) not in recorded:
            continue
        attrs = [a for a in ((fields or {}).get(metric.key) or ())
                 if a.type in ("category", "tags")]
        by_provider.setdefault(metric.provider, []).append(_metric_forms(metric, attrs))
    order = sorted(by_provider, key=lambda p: (
        _STARTER_SOURCES.index(p) if p in _STARTER_SOURCES else len(_STARTER_SOURCES), p))
    out: list[str] = []
    used: set[str] = set()
    taken: list = []
    while len(out) < MAX_CHART_STARTERS and any(by_provider.values()):
        for provider in order:
            items = by_provider[provider]
            if items and len(out) < MAX_CHART_STARTERS:
                forms = items.pop(0)
                fresh = [f for f in forms if f[0] not in used]
                shape, text = min(fresh, key=lambda f: _SHOWCASE.index(f[0])
                                  if f[0] in _SHOWCASE else len(_SHOWCASE)) if fresh else forms[0]
                used.add(shape)
                out.append(text)
                taken.append(forms)
    # At least MIN_CHART_STARTERS when anything is chartable: a small scope is
    # asked again in a chart type not yet shown.
    for forms in taken * MIN_CHART_STARTERS:
        if len(out) >= MIN_CHART_STARTERS:
            break
        extra = next(((sh, t) for sh, t in forms if sh not in used and t not in out), None)
        if extra:
            used.add(extra[0])
            out.append(extra[1])
    return out


#: Longest column name a starter repeats; a header can be a sentence.
_STARTER_NAME_CHARS = 40


def upload_starters(tables) -> list[str]:
    """Starters from the files uploaded to this chat: each table's first
    number column, by its first category (or per month over its first date).
    Read from the file's own headers, so each one can be drawn."""
    out: list[str] = []
    for table in tables:
        def first(kind):
            return next((c["name"][:_STARTER_NAME_CHARS] for c in table.columns
                         if c.get("type") == kind and c.get("name")), None)
        number, category, when = first("number"), first("category"), first("date")
        if number and category:
            text = f"Total {number} by {category}"
        elif number and when:
            text = f"Total {number} per month"
        elif category:
            text = f"Rows by {category}"
        else:
            continue
        if text not in out:
            out.append(text)
    return out


@router.get("/chart-starters")
def list_chart_starters(
    workspace_id: str | None = None,
    conversation_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """Chart mode's starter questions, from this scope's metrics and fields,
    led by the files this person uploaded to ``conversation_id``."""
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    from ..insights import attr_catalog, registry, scopes
    from ..insights import store as insights_store

    try:
        connected = _connected_providers(session.org_id, workspace_id)
    except Exception:  # noqa: BLE001 - starters are a convenience
        logger.warning("chart starters: could not list connections", exc_info=True)
        return {"questions": []}
    metrics = [
        m for p in sorted(connected) for m in registry.for_provider(p)
        if scopes.may_see_metric(m, role=session.role, workspace_id=workspace_id,
                                 org_id=session.org_id, user_id=session.user_id)
    ]
    try:
        fields = attr_catalog.by_metric(attr_catalog.discover_scope(
            org_id=session.org_id, workspace_id=workspace_id, viewer=viewer_for(session),
        ))
    except Exception:  # noqa: BLE001
        fields = None
    from_files: list[str] = []
    uploads = _upload_scope(conversation_id, session)
    if uploads is not None:
        from ..doctables.store import list_upload_tables

        try:
            from_files = upload_starters(
                list_upload_tables(org_id=session.org_id, uploads=uploads))
        except Exception:  # noqa: BLE001 - starters are a convenience
            logger.warning("chart starters: could not list uploaded tables", exc_info=True)
    viewer = viewer_for(session)
    try:
        recorded = insights_store.recorded_kinds(
            org_id=session.org_id, workspace_id=workspace_id, viewer=viewer)
    except Exception:  # noqa: BLE001 - starters are a convenience
        recorded = None
    try:
        from ..doctables.store import list_tables

        tables = list_tables(org_id=session.org_id, workspace_id=workspace_id,
                             viewer=viewer, limit=50)
    except Exception:  # noqa: BLE001
        tables = []
    questions = (from_files + chart_starters(metrics, fields, tables, recorded))[:MAX_CHART_STARTERS]
    return {"questions": questions}


@router.get("/suggestions")
def list_suggestions(
    agent: str | None = None,
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """Starter questions from this scope's connected sources, for any member.

    Document chips come from ingested titles, Code chips from the stored repo
    list. No ``agent`` means every connected source, interleaved; ``agent``
    pins one source.
    """
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not agent:
        return _combined_suggestions(session.org_id, workspace_id, session)

    requested = agent.strip().lower()
    # Chips are built from document titles, so they carry the same access
    # filter the answers do (see `_TITLE_ACCESS_SQL`).
    acl = viewer_for(session).acl()
    if requested == AGENT_GITHUB:
        repos = _github_repos_for_scope(session.org_id, workspace_id)
        return {"agent": AGENT_GITHUB, "questions": build_github_suggestions(repos)}

    if requested == AGENT_SLACK:
        channels = _slack_channel_names_for_scope(session.org_id, workspace_id)
        return {"agent": AGENT_SLACK, "questions": build_slack_suggestions(channels)}

    if requested == AGENT_LINEAR:
        titles = _linear_titles_for_scope(session.org_id, workspace_id, acl)
        return {"agent": AGENT_LINEAR, "questions": build_linear_suggestions(titles)}

    if requested == AGENT_NOTION:
        titles = _titles_for_scope_by_provider(session.org_id, workspace_id, "notion", acl)
        return {
            "agent": AGENT_NOTION,
            "questions": build_policy_suggestions(titles, workspace=workspace_id is not None),
        }

    if requested == AGENT_GOOGLE:
        titles = _titles_for_scope_by_provider(session.org_id, workspace_id, "google", acl)
        return {
            "agent": AGENT_GOOGLE,
            "questions": build_policy_suggestions(titles, workspace=workspace_id is not None),
        }

    titles = _document_titles_for_scope(session.org_id, workspace_id, acl)
    return {
        "agent": AGENT_POLICY,
        "questions": build_policy_suggestions(
            titles, workspace=workspace_id is not None
        ),
    }


def _github_repos_for_scope(org_id: str, workspace_id: str | None = None) -> list[dict]:
    """Repo list from this scope's GitHub connection's ``source_config``.

    Scoped with ``IS NOT DISTINCT FROM``: a space with no GitHub connection gets
    ``[]``, never the org's repos.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT source_config FROM oauth_connections "
            "WHERE org_id = %s AND provider = 'github' "
            "AND workspace_id IS NOT DISTINCT FROM %s",
            (org_id, workspace_id),
        ).fetchone()
    if not row or row[0] is None:
        return []
    config = row[0]
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            return []
    if not isinstance(config, dict):
        return []
    repos = config.get("repos") or []
    return [r for r in repos if isinstance(r, dict)]


def _slack_channel_names_for_scope(
    org_id: str, workspace_id: str | None = None
) -> list[str]:
    """Connected Slack channel names from this scope's ``source_config``; ``[]`` if none picked."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT source_config FROM oauth_connections "
            "WHERE org_id = %s AND provider = 'slack' "
            "AND workspace_id IS NOT DISTINCT FROM %s",
            (org_id, workspace_id),
        ).fetchone()
    if not row or row[0] is None:
        return []
    config = row[0]
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            return []
    if not isinstance(config, dict):
        return []
    names = config.get("channel_names") or {}
    channel_ids = config.get("channel_ids") or []
    if isinstance(names, dict):
        # Preserve the admin's picked order, and fall back to the id when a
        # name is missing so a chip is never rendered as an empty "#".
        return [str(names.get(cid) or cid) for cid in channel_ids] or [
            str(v) for v in names.values()
        ]
    return [str(cid) for cid in channel_ids]


# Chips are built from document titles, so they carry retrieval's access
# filter. `acl` is required; an empty list means scope-public only, never everything.
_TITLE_ACCESS_SQL = "AND " + visibility_predicate(alias=None) + " "


def _document_titles_for_scope(
    org_id: str, workspace_id: str | None, acl: list[str]
) -> list[str]:
    """Newest legacy-doc titles for this org or workspace, as ``acl`` may read."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT title FROM documents "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "AND source_provider IS DISTINCT FROM 'slack' "
            "AND source_provider IS DISTINCT FROM 'linear' "
            "AND source_provider IS DISTINCT FROM 'notion' "
            "AND source_provider IS DISTINCT FROM 'google' "
            + _TITLE_ACCESS_SQL +
            "ORDER BY created_at DESC NULLS LAST "
            "LIMIT 12",
            (org_id, workspace_id, acl),
        ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def _titles_for_scope_by_provider(
    org_id: str, workspace_id: str | None, provider: str, acl: list[str]
) -> list[str]:
    """Newest titles for one provider in this scope, as ``acl`` may read."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT title FROM documents "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "AND source_provider = %s "
            + _TITLE_ACCESS_SQL +
            "ORDER BY created_at DESC NULLS LAST "
            "LIMIT 12",
            (org_id, workspace_id, provider, acl),
        ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def _linear_titles_for_scope(
    org_id: str, workspace_id: str | None, acl: list[str]
) -> list[str]:
    """Ingested Linear issue titles for this org (or one workspace), newest first."""
    return _titles_for_scope_by_provider(org_id, workspace_id, "linear", acl)


@router.get("/conversations")
def list_conversations_route(
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """This person's chats in this scope, all of them.

    Retention is the only limit: a display cap would strand a chat unreachable.
    """
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    rows = conversation_store.list_conversations(
        org_id=session.org_id, user_id=session.user_id, workspace_id=workspace_id
    )
    return {
        "retention_days": conversation_store.DEFAULT_CONVERSATION_TTL_DAYS,
        "conversations": [
            {
                "id": r.id,
                "title": r.title,
                "turn_count": r.turn_count,
                "attachment_count": r.attachment_count,
                "created_at": r.created_at.isoformat(),
                "last_activity_at": r.last_activity_at.isoformat(),
                "pinned": r.pinned,
                "renamed": r.renamed,
            }
            for r in rows
        ],
    }


@router.get("/conversations/{conversation_id}")
def get_conversation_route(
    conversation_id: str,
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """The full transcript.

    404 on nothing: not theirs and gone must look the same.
    """
    turns = conversation_store.get_conversation_turns(
        conversation_id=conversation_id,
        org_id=session.org_id,
        user_id=session.user_id,
        workspace_id=workspace_id,
    )
    if not turns:
        raise HTTPException(status_code=404, detail="No such conversation")
    return {
        "conversation_id": conversation_id,
        "turns": [
            {
                "turn_index": t.turn_index,
                "question": t.question,
                "answer": t.answer,
                "created_at": t.created_at.isoformat(),
                "cited": t.cited,
                "chart": t.chart,
                "meta": t.meta,
            }
            for t in turns
        ],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation_route(
    conversation_id: str,
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """Delete a chat now; turns and attachments cascade."""
    if not conversation_store.delete_conversation(
        conversation_id=conversation_id,
        org_id=session.org_id,
        user_id=session.user_id,
        workspace_id=workspace_id,
    ):
        raise HTTPException(status_code=404, detail="No such conversation")
    return {"deleted": True}


@router.patch("/conversations/{conversation_id}")
def update_conversation_route(
    conversation_id: str,
    body: dict,
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """Rename (``title``; blank resets to the first question) or pin (``pinned``)."""
    kwargs: dict = {}
    if "title" in body:
        if body["title"] is not None and not isinstance(body["title"], str):
            raise HTTPException(status_code=400, detail="title must be text")
        kwargs["title"] = body["title"]
    if "pinned" in body:
        if not isinstance(body["pinned"], bool):
            raise HTTPException(status_code=400, detail="pinned must be true or false")
        kwargs["pinned"] = body["pinned"]
    if not kwargs:
        raise HTTPException(status_code=400, detail="Nothing to change")
    if not conversation_store.update_conversation(
        conversation_id=conversation_id,
        org_id=session.org_id,
        user_id=session.user_id,
        workspace_id=workspace_id,
        **kwargs,
    ):
        raise HTTPException(status_code=404, detail="No such conversation")
    return {"updated": True}


@router.post("/conversations")
def create_conversation(
    body: dict | None = None,
    session: SessionClaims = Depends(get_session),
):
    workspace_id = (body or {}).get("workspace_id")
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    if (body or {}).get("agent") == AGENT_GITHUB:
        raise HTTPException(
            status_code=400,
            detail="GitHub questions are answered standalone and do not use conversations.",
        )

    agent = _select_agent(workspace_id, (body or {}).get("agent"))
    if agent.pipeline.memory is None:
        raise HTTPException(status_code=503, detail="Conversation memory is not enabled")

    conversation_id = agent.pipeline.memory.create_conversation(
        session.org_id, workspace_id=workspace_id, user_id=session.user_id
    )
    return {"conversation_id": conversation_id}


def _deep_would_help(result, attached: list[tuple[str, str, bool]] | None = None) -> bool:
    """A normal-Ask answer that Deep analysis could make more complete.

    Only when there is more to read: a long upload, or gate-passing documents
    behind a refusal, a partial answer or a wide read. Not after a gate miss
    or a withheld document.
    """
    if result.access_restricted:
        return False
    size = sum(len(text) for _, text, _ in attached or [])
    if size > AttachmentSettings.from_env().inline_char_budget:
        return True
    if result.top_score is None or result.top_score < RagSettings.from_env().similarity_threshold:
        return False
    if not result.grounded or getattr(result, "wide_read", False):
        return True
    # A partial answer ends with a "Not covered:" line (the partial rule in
    # app/rag/prompts.py): answered, but the rest may be deeper in the documents.
    return any(line.strip().startswith("Not covered:")
               for line in (getattr(result, "answer", "") or "").splitlines())


#: Shown instead of "I don't know" when the material is there but Ask read
#: too little. Still a refusal, and still a recorded gap.
DEEP_SUGGESTION = (
    "I found related material, but the parts I read don't answer this. "
    "Deep analysis reads it more fully: choose Try Deep analysis below."
)


def _shown_answer(result, deep_hint: bool) -> str:
    return DEEP_SUGGESTION if deep_hint and not result.grounded else result.answer


_PROGRESS_END = object()


def _with_progress(body) -> Iterator[str]:
    """Run ``body`` in a worker and stream its progress lines meanwhile.

    The worker runs in a COPY of this context, so the selections set above reach it.
    """
    out: queue.Queue = queue.Queue()

    def run() -> None:
        progress.use_progress(lambda text: out.put(_sse_event("status", text)))
        try:
            for event in body():
                out.put(event)
        except BaseException as exc:  # noqa: BLE001 -- re-raised on the stream side
            out.put(exc)
        finally:
            out.put(_PROGRESS_END)

    # ponytail: a client that disconnects leaves the worker to finish its one
    # answer; add a cancel flag if abandoned answers ever cost real quota.
    threading.Thread(target=contextvars.copy_context().run, args=(run,), daemon=True).start()
    while (item := out.get()) is not _PROGRESS_END:
        if isinstance(item, BaseException):
            raise item
        yield item


def _sse_event(event: str, data: dict | str) -> str:
    payload = json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


# How often a waiting answer asks again for a shared place.
_PLACE_POLL_SECONDS = 0.25

_PERSON_BUSY = (
    "You already have {n} answers being written. Please wait for one to finish."
)


async def _in_a_slot(
    stream: Iterator[str], settings: RateLimitSettings, user_id: str
) -> AsyncIterator[str]:
    """Cap answers written at once: per person and for everyone (``take_place``).

    Without the shared cap a burst got 429s from outside APIs. Taken inside the
    stream so a client that leaves gives it back. Over your own cap is refused
    at once; a shared waiter is told, and told "busy" past the limit. Waits on
    the event loop: blocking threads starved every sync endpoint.
    """
    mine_key = f"answers:user:{user_id}"
    mine = await asyncio.to_thread(
        take_place, mine_key, settings.chat_max_concurrent_per_person, settings
    )
    if mine is None:
        yield _sse_event(
            "error", {"message": _PERSON_BUSY.format(n=settings.chat_max_concurrent_per_person)}
        )
        return
    shared = None
    try:
        shared = await asyncio.to_thread(
            take_place, "answers:all", settings.chat_max_concurrent, settings
        )
        if shared is None:
            yield _sse_event("status", "Many people are asking right now, waiting for a free slot…")
            loop = asyncio.get_running_loop()
            deadline = loop.time() + settings.chat_queue_wait_seconds
            while shared is None:
                if loop.time() >= deadline:
                    yield _sse_event(
                        "error",
                        {"message": "Many people are asking right now. Please try again in a moment."},
                    )
                    return
                await asyncio.sleep(_PLACE_POLL_SECONDS)
                shared = await asyncio.to_thread(
                    take_place, "answers:all", settings.chat_max_concurrent, settings,
                    log_refusal=False,
                )
        async for event in iterate_in_threadpool(stream):
            yield event
    finally:
        # Given back without awaiting: this also runs when the client leaves
        # and the task is cancelled. Each is one quick call (or a no-op).
        give_back_place("answers:all", shared, settings)
        give_back_place(mine_key, mine, settings)


def _shown_live(result) -> list[dict]:
    """The live reads the answer cites, once per provider (an uncited one earns no chip)."""
    cited = {c.get("document_id") for c in (getattr(result, "cited", None) or [])}
    shown: list[dict] = []
    seen: set[str] = set()
    for read in getattr(result, "live_sources", None) or []:
        doc = read.get("document_id")
        provider = read.get("provider")
        if (doc is not None and doc not in cited) or provider in seen:
            continue
        seen.add(provider)
        shown.append({"provider": provider, "fetched_at": read.get("fetched_at")})
    return shown


def _done_event(conversation_id: str | None, question: str, payload: dict) -> str:
    """The ``done`` event, and who answered saved on the turn it closes.

    So a reopened chat keeps its provenance. Never raises: losing the label
    must not lose the answer.
    """
    if conversation_id:
        try:
            conversation_store.set_last_turn_meta(
                conversation_id, question, conversation_store.meta_for_storage(payload)
            )
        except Exception:  # noqa: BLE001 - see docstring
            logger.warning("could not label the turn", exc_info=True)
    return _sse_event("done", payload)


def _answering_model() -> str | None:
    """The model that produced this answer.

    The endpoint's ``response.model`` when given (true under fallback), else the
    selection. ``None`` on the default path: nothing to disclose.
    """
    return answering_model() or selected_model()


def _upload_scope(conversation_id: str | None, session: SessionClaims | None):
    """Whose uploads Chart mode may chart: this person, this chat. The
    conversation was already checked as theirs by the route."""
    if conversation_id is None or session is None or not session.user_id:
        return None
    from ..doctables.store import UploadScope

    return UploadScope(conversation_id=conversation_id, user_id=session.user_id)


def _conversation_attachments(
    org_id: str, conversation_id: str | None, session: SessionClaims | None
) -> list[tuple[str, str, bool]]:
    """``(filename, text, truncated)`` for this chat's files, oldest first.

    Never raises: a failed read degrades to answering from the corpus.
    """
    if conversation_id is None or session is None:
        return []
    try:
        from ..attachments import load_attachment_texts

        # A flagged file (enforce mode) leads with a warning, so every path
        # that quotes it -- inline, paged, head-only -- carries it too.
        return [
            (a.filename, (ATTACHMENT_WARNING if is_flagged(a.injection_score) else "")
             + (a.content or ""), a.truncated)
            for a in load_attachment_texts(
                org_id=org_id,
                conversation_id=conversation_id,
                user_id=session.user_id,
            )
        ]
    except Exception:  # noqa: BLE001
        logger.warning("Chat: could not load attachments", exc_info=True)
        return []


def _stream_attachment_answer(
    question: str,
    attached: list[tuple[str, str, bool]],
    org_id: str,
    conversation_id: str | None,
    workspace_id: str | None,
    decision,
    session: SessionClaims | None = None,
    deep_mode: bool = False,
) -> Iterator[str]:
    """Answer from the attached files AND the routed source's corpus.

    The routed pipeline runs unchanged (gate, strict prompt, audit); each file
    is in the prompt behind its own "Attached file:" line. Files live on the
    conversation, so they must not replace retrieval for later questions. An
    agent with no corpus (GitHub, Insights) answers from the files alone.
    """
    names = ", ".join(name for name, _, _ in attached)
    agent = _agent_getters().get(decision.agent_key, lambda: None)()
    pipeline = getattr(agent, "pipeline", None)

    try:
        if pipeline is None:
            # GitHub/Insights: live reads and SQL, no chunks to retrieve. The
            # attachment is still the thing in front of the asker.
            result = _agent_getters()[AGENT_POLICY]().pipeline.answer_from_attachments(
                question, attached, org_id=org_id, conversation_id=conversation_id
            )
            reason = f"answered from attached file(s): {names}"
            agent_key = "attachment"
        else:
            result = pipeline.answer(
                question,
                org_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                attachments=attached,
                viewer=viewer_for(session),
            )
            reason = f"{decision.reason} · with attached file(s): {names}"
            agent_key = decision.agent_key
    except ProviderError as exc:
        logger.warning("Chat attachment failure: %s", exc, exc_info=True)
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return

    response = RagPipelineAgent._to_response(result)
    deep_hint = not deep_mode and _deep_would_help(response, attached)
    shown = _shown_answer(response, deep_hint)
    delay = _stream_word_delay_seconds()
    for chunk in _word_chunks(shown):
        yield _sse_event("token", chunk)
        if delay:
            time.sleep(delay)

    if not response.grounded and not response.access_restricted and not getattr(response, "live_withheld", False):
        record_gap(
            org_id=org_id,
            question=question,
            resolved_question=response.resolved_question,
            answer=response.answer,
            workspace_id=workspace_id,
            user_id=session.user_id if session else None,
            conversation_id=conversation_id,
            agent=agent_key,
            gate_score=response.top_score,
        )

    yield _done_event(
        conversation_id,
        question,
        {
            "answer": shown,
            "grounded": response.grounded,
            "source": response.source,
            "citations": [
                {"content": c.content, "reference": c.reference, "score": c.score}
                for c in response.citations
            ],
            "resolved_question": response.resolved_question,
            "latency_ms": response.latency_ms,
            "agent": agent_key,
            # Names the routed source AND the files, because with both in one
            # prompt "where did this come from?" has two answers.
            "routing_reason": reason,
            # The files in this prompt, so the pill does not credit only the routed agent.
            "attachments": [name for name, _, _ in attached],
            "model": _answering_model(),
            "chart": None,
            "chart_period": None,
            "live_sources": _shown_live(response),
            # The documents behind the answer's [n] markers; the UI draws a
            # chip only for a number listed here and strips the rest.
            "cited": list(getattr(response, "cited", None) or []),
            "deep_hint": deep_hint,
        },
    )


def _previous_question(
    org_id: str,
    conversation_id: str | None,
    workspace_id: str | None,
    session: SessionClaims | None,
) -> str | None:
    """The last question in this conversation, for routing a follow-up; never raises."""
    if not conversation_id or session is None:
        return None
    try:
        turns = conversation_store.get_conversation_turns(
            conversation_id=conversation_id,
            org_id=org_id,
            user_id=session.user_id,
            workspace_id=workspace_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning("could not read the previous turn for routing", exc_info=True)
        return None
    return turns[-1].question if turns else None


def _start_graph_plan(org_id, workspace_id, question, session):
    """Start this question's graph walk on the routing pool, or return None.

    Needs a real viewer: no session, no graph.
    """
    if session is None:
        return None
    try:
        settings = GraphSettings.from_env()
        if not settings.retrieval_enabled:
            return None
        viewer = viewer_for(session)
        return _ROUTING_POOL.submit(
            contextvars.copy_context().run,
            graph_plan.build_plan,
            org_id,
            workspace_id,
            question,
            viewer,
            settings=settings,
        )
    except Exception:  # noqa: BLE001 - the graph may only ever add
        logger.warning("graph plan not started", exc_info=True)
        return None


#: How long the chat edge waits for a plan still being built after routing.
#: Past it, retrieval simply walks on its own, exactly as it did before plans.
_GRAPH_PLAN_WAIT_SECONDS = 1.5


def _graph_plan_result(future):
    if future is None:
        return None
    try:
        return future.result(timeout=_GRAPH_PLAN_WAIT_SECONDS)
    except Exception:  # noqa: BLE001 - slow or failed: the answer goes on without it
        logger.info("graph plan not ready; continuing without it")
        return None


def _named_connected_tools(question: str, org_id: str, workspace_id: str | None) -> set[str]:
    """Every indexed tool the question NAMES that is connected in this scope.

    Not limited to what the graph reached; only connected tools, so the pill
    never claims one that contributed nothing.
    """
    named = named_providers(question, EMBEDDED_PROVIDERS)
    if not named:
        return set()
    try:
        return named & _connected_providers(org_id, workspace_id)
    except Exception:  # noqa: BLE001 - a named tool may only ever add
        logger.warning("could not read connections for a named tool", exc_info=True)
        return set()


def _graph_connected_enabled() -> bool:
    try:
        return GraphSettings.from_env().connected_enabled
    except Exception:  # noqa: BLE001
        return False


#: Values written under a counted answer; the chart beside it has the rest.
_COUNTED_LINES = 12


def _counted_answer(result) -> str:
    """A count asked in Ask, answered by SQL as a chart with its numbers written out.

    Every value is the chart's own (``describe.format_value``).
    """
    from ..chartrender.matplotlib_renderer import format_bucket
    from ..insights.describe import format_value

    chart = getattr(result, "chart", None)
    points = (chart or {}).get("points") if isinstance(chart, dict) else None
    if not points:
        return result.answer
    period = getattr(result, "chart_period", None) or "month"
    lines = []
    for point in points[:_COUNTED_LINES]:
        when = format_bucket(point["bucket"], period) if point.get("bucket") else ""
        label = " · ".join(str(p) for p in (point.get("group"), point.get("series"), when) if p)
        if label and point.get("value") is not None:
            lines.append(f"- {label}: {format_value(point['value'], chart.get('unit') or '')}")
    more = [f"- …and {len(points) - _COUNTED_LINES} more in the chart"] \
        if len(points) > _COUNTED_LINES else []
    if lines and len(points) > 1 and chart.get("measure") in ("count", "sum"):
        # "How many interviews?" asked for ONE number: a small model read the
        # three months off the list and never added them up.
        total = sum(float(p["value"]) for p in points if p.get("value") is not None)
        more.append(f"- Total: {format_value(total, chart.get('unit') or '')}")
    return "\n".join([result.answer, "", *lines, *more]) if lines else result.answer


def _refusal_retry(decision, plan, org_id: str, workspace_id: str | None):
    """``(decision, plan, tools)`` for asking a refusal once more, elsewhere
    (``routing.refusal_retry``), or ``None``. The retried tools' own searches
    join, so no graph is needed. One retry, never a loop; Slack runs it too."""
    retry = refusal_retry(decision) if _graph_connected_enabled() else None
    if retry is None:
        return None
    agent, also = retry
    base = plan if plan is not None else graph_plan.GraphPlan(org_id, workspace_id)
    return (dataclasses.replace(decision, agent_key=agent),
            base.connected({agent} | also, search=also), {agent} | also)


def _invoke_with_plan(graph_input: dict, plan, worked: tuple = ()):
    """Run the agent graph with ``plan`` as this request's graph plan, and
    ``worked`` (``insights.table_answers``) as its worked-out figures."""
    from ..insights.table_answers import reset_answers, use_answers

    token = graph_plan.use_plan(plan)
    figures = use_answers(worked)
    try:
        return _agent_graph().invoke(graph_input)["response"]
    finally:
        reset_answers(figures)
        graph_plan.reset_plan(token)


def _keep_standalone_turn(
    agent_key: str,
    conversation_id: str | None,
    question: str,
    answer: str,
    cited: list | None = None,
    chart: dict | None = None,
) -> None:
    """Save a turn for an agent outside ``RagPipeline`` (GitHub, charts).

    Otherwise the chat vanishes from history on reload. Never drops the answer.
    """
    if agent_key not in ("github", "insights") or not conversation_id or not (answer or "").strip():
        return
    try:
        from ..memory import build_conversation_store

        store = build_conversation_store()
        params = store.append_turn.__code__.co_varnames
        if "chart" in params:
            # The chart is saved with the turn, so a reopened chat shows it
            # again instead of the text alone.
            store.append_turn(conversation_id, question, answer, cited or None, chart)
        elif "cited" in params:
            store.append_turn(conversation_id, question, answer, cited or None)
        else:
            store.append_turn(conversation_id, question, answer)
    except Exception:  # noqa: BLE001 - the answer already exists; losing the save is the old bug
        logger.warning("could not save %s turn", agent_key, exc_info=True)


def _drop_refusal_turn(org_id, conversation_id, question, answer) -> None:
    if not conversation_id:
        return
    try:
        conversation_store.delete_last_turn_if(
            conversation_id=conversation_id, org_id=org_id, question=question, answer=answer
        )
    except Exception:  # noqa: BLE001 - a duplicate turn beats a failed answer
        logger.warning("could not drop the refused turn", exc_info=True)


def _stream_answer(
    question: str,
    org_id: str,
    conversation_id: str | None,
    workspace_id: str | None = None,
    requested_agent: str | None = None,
    model: str | None = None,
    session: SessionClaims | None = None,
    chart_mode: bool = False,
    deep_mode: bool = False,
    extra_effort: bool = False,
) -> Iterator[str]:
    # Who is asking, for live reads (app/livetools). Set inside the generator,
    # like `use_model`.
    live_token = use_live_request(
        LiveRequest(
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=session.user_id if session else None,
            conversation_id=conversation_id,
        )
    )
    # Personal memory shapes how the question is read; only the question is read
    # for new facts.
    facts, memory_turn = _start_personal_memory(question, org_id, conversation_id, session)
    facts_token = personal_memory.use_asker_facts(tuple((f.kind, f.text) for f in facts))
    deep_token = use_deep_read(deep_mode, extra=extra_effort)
    try:
        yield from _with_progress(lambda: _stream_answer_body(
            question, org_id, conversation_id, workspace_id, requested_agent,
            model, session, memory_turn, chart_mode=chart_mode, deep_mode=deep_mode,
        ))
    finally:
        # Starlette may close the generator from another copied context, where
        # the token is not valid; there the copy dies with the call anyway.
        for reset, token in ((reset_live_request, live_token),
                             (personal_memory.reset_asker_facts, facts_token),
                             (reset_deep_read, deep_token)):
            try:
                reset(token)
            except ValueError:
                pass


#: Extraction runs beside the answer; the answer waits at most this long
#: for it before `done`, so "Remembered: ..." can be shown in the moment.
_MEMORY_GRACE_SECONDS = 1.5
_MEMORY_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="personal-memory")


@dataclass
class _MemoryTurn:
    org_id: str
    user_id: str
    #: Facts saved earlier but never shown (their extraction outlived the
    #: grace period): announced on THIS answer, so saving is never silent.
    pending: list
    future: object | None = None


def _start_personal_memory(question, org_id, conversation_id, session):
    """``(facts, turn|None)``. Never raises; off => ``([], None)``."""
    user_id = session.user_id if session else None
    try:
        if not personal_memory.is_active(org_id, user_id):
            return [], None
        facts = personal_memory.list_facts(org_id, user_id)
    except Exception:  # noqa: BLE001 - memory may only ever add
        logger.warning("personal memory unavailable", exc_info=True)
        return [], None
    turn = _MemoryTurn(org_id, user_id, pending=[f for f in facts if not f.announced])
    if conversation_id and personal_memory.worth_reading(question):
        turn.future = _MEMORY_POOL.submit(
            personal_memory.remember_from_question, question, org_id=org_id,
            user_id=user_id, conversation_id=conversation_id,
            known=tuple(f.text for f in facts),
        )
    return facts, turn


def _remembered(turn) -> list[dict]:
    """Facts to show as "Remembered: ... · Undo": any saved earlier but never
    shown, plus this question's if extraction finished within the grace. A
    slower one stays unannounced and appears on the NEXT answer."""
    if turn is None:
        return []
    facts = list(turn.pending)
    if turn.future is not None:
        try:
            facts += list(turn.future.result(timeout=_MEMORY_GRACE_SECONDS))
        except Exception:  # noqa: BLE001 - includes the timeout
            pass
    try:
        personal_memory.mark_announced(turn.org_id, turn.user_id, [f.id for f in facts])
    except Exception:  # noqa: BLE001 - shown twice beats not shown
        logger.warning("could not mark memory announced", exc_info=True)
    return [{"id": f.id, "text": f.text} for f in facts]


def _stream_answer_body(
    question: str,
    org_id: str,
    conversation_id: str | None,
    workspace_id: str | None,
    requested_agent: str | None,
    model: str | None,
    session: SessionClaims | None,
    memory_turn=None,
    chart_mode: bool = False,
    deep_mode: bool = False,
) -> Iterator[str]:
    # Set inside the generator: Starlette runs it in a threadpool, so a ContextVar
    # set in the route may not reach it, and a pooled thread must not leak it.
    use_model(model, org_id=org_id)
    # Who pays for this answer's model calls, for the same reason.
    charge_to(session.user_id if session else None, org_id)
    # Logged, never refused (see guard/live.py for the measured reason).
    watch_question(question)


    # Attachments ride along with the routed source (`_stream_attachment_answer`);
    # they no longer replace routing.
    attached = _conversation_attachments(org_id, conversation_id, session)

    # Decide which agent answers before the graph runs (`choose_agent` honours an
    # explicit `requested_agent`). The knowledge graph is walked ONCE beside
    # routing (app/graph/plan.py); None when GRAPH_RETRIEVAL_ENABLED is off.
    progress.report("Choosing where to look")
    plan_future = _start_graph_plan(org_id, workspace_id, question, session)
    decision = choose_agent(
        question,
        org_id,
        workspace_id=workspace_id,
        requested_agent=requested_agent,
        context=_previous_question(org_id, conversation_id, workspace_id, session),
        graph_plan=plan_future,
        viewer=viewer_for(session),
        chart_mode=chart_mode,
        uploads=_upload_scope(conversation_id, session),
        user_id=session.user_id,
    )
    plan = _graph_plan_result(plan_future)
    # The live-data verdict rides the request note, reset when the stream ends.
    current = current_live_request()
    if current is not None:
        use_live_request(dataclasses.replace(current, needs_live=getattr(decision, "needs_live", None)))
    connected: set[str] | None = None
    if plan is not None and _graph_connected_enabled():
        connected = graph_plan.connected_tools(
            plan, decision.agent_key, _named_connected_tools(question, org_id, workspace_id)
        )
        if connected:
            plan = plan.connected(connected, search=connected - {decision.agent_key})
    progress.searching(decision.agent_key, connected)
    logger.info(
        "Chat routing: agent=%s reason=%s scores=%s",
        decision.agent_key,
        decision.reason,
        decision.scores,
    )

    # Routed FIRST, then blended: the router picks which corpus supports the
    # question, and the attached files join whatever it retrieves.
    if attached and not chart_mode:
        yield from _stream_attachment_answer(
            question, attached, org_id, conversation_id, workspace_id, decision, session,
            deep_mode=deep_mode,
        )
        return

    spec = getattr(decision, "chart_spec", None)
    chart_spec = spec_to_dict(spec) if spec is not None else None

    graph_input = {
        "question": question,
        "org_id": org_id,
        "conversation_id": conversation_id,
        "workspace_id": workspace_id,
        "requested_agent": decision.agent_key,
        "stream": True,
        "chart_spec": chart_spec,
        "chart_refusal": getattr(decision, "chart_refusal", None),
        "user_id": session.user_id if session else None,
        "role": session.role if session else None,
        "viewer": viewer_for(session),
    }
    try:
        result = _invoke_with_plan(graph_input, plan, decision.table_answers)
        # Escalation: the routed tool refused and the graph shows readable documents
        # in OTHER tools. One retry, never on a withheld document. The refusal turn
        # is removed first so the chat keeps one turn per question.
        retry_tools = (
            graph_plan.escalation_tools(plan, decision.agent_key)
            if plan is not None and _graph_connected_enabled()
            and not result.grounded and not result.access_restricted
            else None
        )
        retry_plan = plan.connected(retry_tools) if retry_tools else None
        if retry_plan is None and not result.grounded and not result.access_restricted:
            elsewhere = _refusal_retry(decision, plan, org_id, workspace_id)
            if elsewhere is not None:
                decision, retry_plan, retry_tools = elsewhere
                graph_input = {**graph_input, "requested_agent": decision.agent_key}
        if retry_plan is not None:
            _drop_refusal_turn(org_id, conversation_id, question, result.answer)
            connected = retry_tools
            progress.searching(decision.agent_key, retry_tools)
            result = _invoke_with_plan(graph_input, retry_plan)
    except LLMProviderError as exc:
        logger.warning("Chat LLM failure: %s", exc, exc_info=True)
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return
    except ProviderError as exc:
        logger.warning("Chat provider failure: %s", exc, exc_info=True)
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return

    if decision.reason == "count-chart":
        result = dataclasses.replace(result, answer=_counted_answer(result))
    # A connected retry was answered by the pipeline, which already saved its
    # own turn. Saving again would put the question in the history twice.
    if not retry_tools:
        _keep_standalone_turn(
            decision.agent_key, conversation_id, question, result.answer,
            getattr(result, "cited", None),
            conversation_store.chart_for_storage(
                getattr(result, "chart", None), getattr(result, "chart_period", None)),
        )

    # An ungrounded answer is a documentation gap, recorded here: the one place
    # that sees every way to be ungrounded and the `user_id`. Not gaps: a withheld
    # document (access, not missing docs), a live read withheld as not found or
    # inaccessible, and a Chart-mode reply that is not a missing chart.
    if (not result.grounded and not result.access_restricted
            and not getattr(result, "live_withheld", False)
            and decision.reason not in NOT_CHART_GAPS):
        record_gap(
            org_id=org_id,
            question=question,
            resolved_question=result.resolved_question,
            answer=result.answer,
            workspace_id=workspace_id,
            user_id=session.user_id if session else None,
            conversation_id=conversation_id,
            agent=decision.agent_key,
            gate_score=result.top_score,
        )

    deep_hint = not deep_mode and not chart_mode and _deep_would_help(result)
    shown = _shown_answer(result, deep_hint)
    delay = _stream_word_delay_seconds()
    for chunk in _word_chunks(shown):
        yield _sse_event("token", chunk)
        if delay:
            time.sleep(delay)
    yield _done_event(
        conversation_id,
        question,
        {
            "answer": shown,
            "grounded": result.grounded,
            "source": result.source,
            "citations": [
                {"content": c.content, "reference": c.reference, "score": c.score}
                for c in result.citations
            ],
            "resolved_question": result.resolved_question,
            "latency_ms": result.latency_ms,
            # Which agent answered, and why, so the answer is attributable and a misroute visible.
            "agent": decision.agent_key,
            "routing_reason": "graph-connected" if connected else decision.reason,
            # Every tool a connected answer read, for the pill.
            "connected_providers": sorted(connected) if connected else None,
            # The model that actually answered, never "auto".
            "model": _answering_model(),
            "chart": getattr(result, "chart", None),
            "chart_period": getattr(result, "chart_period", None),
            # Which connectors answered LIVE, and when. Empty when nothing was
            # refreshed -- the indexed copy answered.
            "live_sources": _shown_live(result),
            "cited": list(getattr(result, "cited", None) or []),
            # A text question asked in Chart mode: offer to turn it off.
            "ask_hint": decision.reason == "chart-mode-text-question",
            # A chart ask with Chart mode off, or a count answered in words: offer Chart
            # mode (decided by the question check, not word lists).
            "chart_hint": (
                not chart_mode and (
                    decision.reason == "chart-mode-off"
                    or (decision.agent_key != INSIGHTS_KEY
                        and getattr(decision, "chart_ask", None) == "count")
                )
            ),
            "deep_hint": deep_hint,
            # Personal memory saved from this question, announced so saving is
            # never silent; the pill offers Undo.
            "remembered": _remembered(memory_turn),
        },
    )


@router.post("/stream")
def chat_stream(
    body: dict,
    session: SessionClaims = Depends(get_session),
):
    limit_question(session, body.get("model") if isinstance(body.get("model"), str) else None)

    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="A question is required")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"That question is too long ({len(question)} characters, "
                f"limit {MAX_QUESTION_CHARS}). Ask it in a shorter form."
            ),
        )

    requested_agent = body.get("agent")
    # Validated before the stream starts: afterwards a raise is a truncated 200,
    # not a 400. The model string is untrusted input.
    model = body.get("model")
    if not catalog.is_selectable(model) and not _is_own_model(session.org_id, model):
        # Fails closed: an unknown id must never reach an outbound call.
        raise HTTPException(status_code=400, detail="Unknown model")

    workspace_id = body.get("workspace_id")
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    conversation_id = body.get("conversation_id")
    if conversation_id is not None and not _conversation_belongs_to_scope(
        conversation_id, session.org_id, workspace_id, session.user_id
    ):
        raise HTTPException(status_code=404, detail="No such conversation for this organization")

    stream = _stream_answer(
        question,
        session.org_id,
        conversation_id,
        workspace_id=workspace_id,
        requested_agent=requested_agent,
        model=model,
        session=session,
        # Chat's Chart and Deep analysis toggles. Anything else is normal Ask.
        chart_mode=body.get("mode") == "chart",
        deep_mode=body.get("mode") in ("deep", "deep_extra"),
        extra_effort=body.get("mode") == "deep_extra",
    )
    stream = _in_a_slot(stream, RateLimitSettings.from_env(), session.user_id)
    return StreamingResponse(stream, media_type="text/event-stream")
