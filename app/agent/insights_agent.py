"""The Insights agent: counted charts, never RAG.

Same ``Agent`` contract as GitHub: question in, structured response out, no
``RagPipeline``. Grounding is structural — numbers come from SQL over
``activity_facts``. The model only selected the registry key (and optionally a
shape we can draw); it never produced a total.

A chart-shaped question that cannot be counted returns the refusal as the
answer and ``grounded=False``. Falling through to retrieval would invent a
number from chunk text.
"""

from __future__ import annotations

import logging
from datetime import datetime
from collections.abc import Iterator

from ..core.answer_sources import SOURCE_NONE
from ..core.exceptions import ProviderError
from ..core.streaming import chunk_answer
from ..insights import registry, scopes, store
from ..insights.facts import DOCUMENT_PROVIDERS, record_document_facts
from ..insights import attr_catalog, query
from ..insights.resolve import ChartSpec, CannotChart, spec_from_dict
from ..vectorstore.base import Viewer
from .base import Agent, AgentResponse
from ..security.links import enforce_link_provenance

logger = logging.getLogger(__name__)


class InsightsAgent(Agent):
    """Answers visual Ask turns by running a validated ``ChartSpec``."""

    def answer(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        viewer: Viewer | None = None,
        spec: ChartSpec | dict | None = None,
        refusal: str | None = None,
        user_id: str | None = None,
        role: str | None = None,
    ) -> AgentResponse:
        # Every count, hover row, subject list and "measured since" date below
        # is narrowed to documents this viewer may open (`store._viewer_filter`)
        # -- a chart is a summary of documents and must not summarise ones the
        # asker cannot read.
        del conversation_id, question  # Spec is already resolved; question is untrusted.
        if refusal:
            return AgentResponse(
                answer=refusal, grounded=False, source=SOURCE_NONE, chart=None
            )
        parsed = _as_spec(spec)
        if parsed is None:
            return AgentResponse(
                answer=(
                    "**This can't be shown as a chart**\n"
                    "Charts are built from activity in your connected apps and "
                    "from figures in document tables, not from topics in text. "
                    "To ask what a document says, ask without \"chart\"."
                ),
                grounded=False,
                source=SOURCE_NONE,
                chart=None,
            )
        if parsed.table_id:
            return _answer_table(parsed, org_id=org_id, workspace_id=workspace_id,
                                 viewer=viewer)
        try:
            panel, period = _run_spec(
                parsed,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id or "",
                role=role or "member",
                viewer=viewer,
            )
        except CannotChart as exc:
            return AgentResponse(
                answer=str(exc), grounded=False, source=SOURCE_NONE, chart=None
            )
        except ProviderError:
            return AgentResponse(
                answer="Could not run that chart.",
                grounded=False,
                source=SOURCE_NONE,
                chart=None,
            )

        points = panel.get("points") or []
        if not points:
            panel, period = _backfill_and_retry(
                parsed, panel, period,
                org_id=org_id, workspace_id=workspace_id,
                user_id=user_id or "", role=role or "member", viewer=viewer,
            )
            points = panel.get("points") or []

        caption = _caption(
            parsed, panel, points,
            org_id=org_id, workspace_id=workspace_id,
            days=scopes.WINDOW_DAYS.get(period, 0),
            metric=registry.METRICS.get(parsed.metric),
            viewer=viewer,
        )
        return AgentResponse(
            answer=caption,
            grounded=True,
            source=panel["provider"],
            chart=panel,
            chart_period=period,
        )

    def answer_stream(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        viewer: Viewer | None = None,
        spec: ChartSpec | dict | None = None,
        refusal: str | None = None,
        user_id: str | None = None,
        role: str | None = None,
    ) -> tuple[Iterator[str], AgentResponse]:
        response = self.answer(
            question,
            org_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            viewer=viewer,
            spec=spec,
            refusal=refusal,
            user_id=user_id,
            role=role,
        )
        return chunk_answer(response.answer), response


def _answer_table(spec: ChartSpec, *, org_id, workspace_id, viewer) -> AgentResponse:
    """A chart of a table inside a document, as an AgentResponse."""
    try:
        panel, period = run_table_spec(
            spec, org_id=org_id, workspace_id=workspace_id, viewer=viewer,
        )
    except CannotChart as exc:
        return AgentResponse(answer=str(exc), grounded=False, source=SOURCE_NONE, chart=None)
    except ProviderError:
        return AgentResponse(answer="Could not run that chart.", grounded=False,
                             source=SOURCE_NONE, chart=None)
    caption = panel["title"] if panel["points"] else (
        f"{panel['title']}. No rows in that table match."
    )
    return AgentResponse(answer=caption, grounded=bool(panel["points"]),
                         source=panel["provider"], chart=panel, chart_period=period)


def run_table_spec(spec: ChartSpec, *, org_id, workspace_id, viewer) -> tuple[dict, str]:
    """Run a document-table chart. Raises ``CannotChart`` / ``ProviderError``.

    The table is looked up AGAIN with the viewer, here, at run time: the
    resolver offered it through the access predicate a moment ago, but a spec
    can also arrive as a stored dict, and a table must never be more visible
    than its document. No viewer means no table chart at all -- the
    unrestricted reading is for ingest and eval, never for a person.
    """
    from ..doctables import store as table_store

    if viewer is None:
        raise CannotChart("I can't chart that here.")
    table = table_store.get_table(
        spec.table_id, org_id=org_id, workspace_id=workspace_id, viewer=viewer,
    )
    if table is None:
        # Deleted, re-indexed or not shared with this person: indistinguishable
        # on purpose, and no title is repeated back.
        raise CannotChart("That table is no longer available to chart.")

    # The resolver checked these against the table it offered; a spec can
    # also arrive as a stored dict, so the column TYPES are checked again --
    # a sum over a text column is a SQL cast error, not a chart.
    def _need(key, types, role):
        col = table.column(key) if key else None
        if key and (col is None or col.get("type") not in types):
            raise CannotChart(f"I can't chart it that way: that column can't be the {role}.")
    _need(spec.group_by, ("category", "date"), "breakdown")
    _need(spec.split_by, ("category",), "second breakdown")
    if (spec.measure or "count") != "count":
        if not spec.value:
            raise CannotChart("I can't chart it that way: there is no number column to add up.")
        _need(spec.value, ("number",), "value to add up")
    for key, _ in spec.filters:
        _need(key, ("category",), "filter")
    filters = tuple(
        (key, _match_table_value(table, key, raw)) for key, raw in spec.filters
    )
    try:
        points = table_store.run_table_query(
            table, measure=spec.measure or "count", value_key=spec.value,
            group_key=spec.group_by, split_key=spec.split_by, period=spec.period,
            filters=filters,
        )
    except ValueError as exc:
        raise CannotChart(f"I can't chart it that way. {exc}") from exc

    def name(key):
        col = table.column(key) if key else None
        return col["name"] if col else key

    value_col = table.column(spec.value) if spec.value else None
    measure = spec.measure or "count"
    what = "Rows" if measure == "count" else f"{measure.title()} of {name(spec.value)}"
    title = what
    if spec.group_by:
        title += f" by {name(spec.group_by)}"
        if spec.split_by:
            title += f" and {name(spec.split_by)}"
    for key, value in filters:
        title += f" — {name(key)}: {value}"
    title += f" — {table.name}"
    if table.origin == "text":
        # In the title, not only the caveat: figures an AI read from prose
        # must never look like figures from a real table at a glance.
        title += " (taken from text)"

    group_col = table.column(spec.group_by) if spec.group_by else None
    by_date = bool(group_col and group_col.get("type") == "date")
    notes = [f"From the table in \"{table.document_title}\"."] + list(table.notes)
    if value_col and value_col.get("unparsed"):
        notes.append(f"{value_col['unparsed']} {name(spec.value)} cells were not "
                     "numbers and are left out.")
    if table.truncated:
        notes.append("The table was longer than we keep; later rows are not counted.")

    unit = "rows" if measure == "count" else (
        (value_col or {}).get("unit") or name(spec.value)
    )
    panel = {
        "id": f"table:{table.id}:{spec.group_by or '-'}:{spec.split_by or '-'}:"
              f"{measure}:{spec.value or '-'}:"
              + ",".join(f"{k}={v}" for k, v in filters),
        "provider": table.provider,
        "title": title,
        "focus": None,
        "chart": spec.chart,
        # On a DATE breakdown the dates are the time axis and the split (if
        # any) is the category; otherwise the breakdown is the category axis.
        "group_by": (spec.split_by if by_date else spec.group_by),
        "split_by": (None if by_date else spec.split_by),
        "filters": [list(f) for f in filters],
        "unit": unit,
        "caveat": " ".join(notes),
        "points": [
            {"bucket": p.bucket or "", "group": p.group, "series": p.series,
             "value": p.value}
            for p in points
        ],
        "details": _table_details(table, filters, date_key=spec.group_by if by_date else None),
        "measured_since": None,
        "table": {"id": table.id, "name": table.name, "document": table.document_title,
                  "origin": table.origin},
    }
    return panel, spec.period


def _match_table_value(table, key, raw) -> str:
    """A typed filter value -> a value that has rows, or a refusal naming them."""
    from ..doctables import store as table_store

    values = table_store.list_values(table, key)
    wanted = raw.strip().lower()
    exact = [v for v in values if v.lower() == wanted]
    if exact:
        return exact[0]
    squashed = _squash(wanted)
    partial = [v for v in values if squashed and squashed in _squash(v)]
    if len(partial) == 1:
        return partial[0]
    col = table.column(key) or {}
    named = enforce_link_provenance(raw, [], ())
    if len(partial) > 1:
        raise CannotChart(f"\"{named}\" matches more than one: {_listed(partial)}. Which one?")
    raise CannotChart(
        f"{table.name} has no {col.get('name', 'value')} \"{named}\". "
        f"What it has: {_listed(values)}."
    )


def _table_details(table, filters, *, date_key) -> list[dict]:
    """The rows behind the bars, for the hover. Never fatal."""
    from ..doctables import store as table_store

    try:
        rows = table_store.list_rows(table, filters=filters)
    except ProviderError:
        return []
    names = [c.get("name", "") for c in table.columns]
    keys = [c.get("key") for c in table.columns]
    out = []
    for cells, raw, quote in rows:
        shown = " · ".join(
            f"{names[i]}: {raw[int(keys[i][1:])]}"
            for i in range(len(keys))
            if keys[i] and int(keys[i][1:]) < len(raw) and raw[int(keys[i][1:])]
        )
        if quote:
            # A figure read from prose shows the sentence it came from, so a
            # reader can check it against the document in one glance.
            shown = f"“{quote}”"
        row = {
            "subject": shown[:300],
            "url": table.source_uri,
            # Cell values by column key, so the hover matches a bar on the
            # column it is grouped by (Chart.tsx `fieldOf`).
            "attrs": {k: str(v) for k, v in cells.items()},
        }
        if date_key and cells.get(date_key):
            row["at"] = str(cells[date_key])
        out.append(row)
    return out


def _as_spec(spec: ChartSpec | dict | None) -> ChartSpec | None:
    if spec is None:
        return None
    if isinstance(spec, ChartSpec):
        return spec
    try:
        return spec_from_dict(spec)
    except (KeyError, TypeError, ValueError):
        return None


def _backfill_and_retry(
    spec: ChartSpec,
    panel: dict,
    period: str,
    *,
    org_id: str,
    workspace_id: str | None,
    user_id: str,
    role: str,
    viewer: Viewer | None = None,
) -> tuple[dict, str]:
    """Fill facts from the index for this scope, then re-run the metric.

    Ask must not wait for the next ingest (or a healthy tick) to notice
    that ``documents`` already has rows. GitHub has no documents, so this
    is a no-op for those metrics.
    """
    try:
        metric = registry.get(spec.metric)
    except KeyError:
        return panel, period
    if metric.provider not in DOCUMENT_PROVIDERS:
        return panel, period
    try:
        record_document_facts(
            org_id, provider=metric.provider, workspace_id=workspace_id,
        )
    except Exception:  # noqa: BLE001 - empty chart beats a failed answer
        logger.warning(
            "insights: lazy fact backfill failed for %s org %s",
            metric.provider, org_id, exc_info=True,
        )
        return panel, period
    try:
        return _run_spec(
            spec, org_id=org_id, workspace_id=workspace_id,
            user_id=user_id, role=role, viewer=viewer,
        )
    except (CannotChart, ProviderError):
        return panel, period


def _details(
    spec, *, org_id, workspace_id, days, focus, viewer=None, filters=(), attrs=None,
) -> list[dict]:
    """Never fatal: a chart without its rows is still a chart, and losing the
    answer to keep the annotation would be the wrong trade."""
    try:
        facts = store.list_facts(
            spec.metric, org_id=org_id, workspace_id=workspace_id,
            days=days, focus=focus, viewer=viewer, filters=filters, attrs=attrs,
        )
    except (ProviderError, KeyError):
        logger.warning("insights: could not read details of %s", spec.metric)
        return []
    return [
        {
            "subject": f.subject,
            "actor": f.actor,
            "state": f.state,
            "at": f.occurred_at,
            **({"attrs": f.attrs} if getattr(f, "attrs", None) else {}),
            "url": f.url,
        }
        for f in facts
    ]


def _buckets(points) -> list[str]:
    seen = []
    for point in points:
        if point.bucket not in seen:
            seen.append(point.bucket)
    return seen


#: A filled time axis longer than this is left as it was: a daily chart over
#: half a year is 180 mostly-empty slots, which is noise, not context.
MAX_FILLED_BUCKETS = 60


def _bucket_start(when, period: str):
    """``date_trunc(period, when)`` as Postgres does it (weeks start Monday)."""
    from datetime import timedelta

    day = when.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return day
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    if period == "quarter":
        return day.replace(day=1, month=3 * ((day.month - 1) // 3) + 1)
    return None


def _next_bucket(start, period: str):
    from datetime import timedelta

    if period == "day":
        return start + timedelta(days=1)
    if period == "week":
        return start + timedelta(days=7)
    step = 1 if period == "month" else 3
    month = start.month - 1 + step
    return start.replace(year=start.year + month // 12, month=month % 12 + 1)


def _fill_gaps(points, period: str, since, until):
    """Every period from ``since`` to ``until``, a quiet one at zero.

    Only the buckets with rows came back from SQL, so one active week drew
    as a lone column with nothing around it. Charting tools draw the whole
    range; so does this -- but only from ``since`` (when this tool's data
    begins, "measured since"), because a week before anything was recorded
    is unknown, not zero. Per series, for a time chart only.
    """
    from datetime import datetime

    if not points or since is None:
        return points
    # Truncate in the zone the database bucketed in, or a filled week would
    # sit a few hours off a real one and read as two.
    zone = datetime.fromisoformat(points[0].bucket).tzinfo
    if zone is not None:
        since, until = since.astimezone(zone), until.astimezone(zone)
    start = _bucket_start(since, period)
    end = _bucket_start(until, period)
    if start is None or end is None or start > end:
        return points
    slots = []
    cursor = start
    while cursor <= end:
        slots.append(cursor)
        if len(slots) > MAX_FILLED_BUCKETS:
            return points
        cursor = _next_bucket(cursor, period)
    have = {(datetime.fromisoformat(p.bucket), p.group, p.series) for p in points}
    series = {(p.group, p.series) for p in points}
    filled = list(points)
    for slot in slots:
        for group, name in series:
            if (slot, group, name) not in have:
                filled.append(store.Point(bucket=slot.isoformat(), group=group,
                                          series=name, value=0))
    return sorted(filled, key=lambda p: p.bucket)


def _span_days(points) -> int:
    """How far apart the rows we already found are.

    A finer period comes with a shorter default window, and narrowing the
    window while narrowing the bucket can drop the very rows we are trying to
    show -- July's commits are outside a 45-day daily window in September.
    """
    buckets = _buckets(points)
    if not buckets:
        return 0
    try:
        stamps = [datetime.fromisoformat(b) for b in buckets]
    except ValueError:
        return 0
    oldest = min(stamps)
    now = datetime.now(oldest.tzinfo) if oldest.tzinfo else datetime.now()
    return max(0, (now - oldest).days + 2)


def _resolve_focus(spec, metric, *, org_id, workspace_id, days, viewer=None) -> str | None:
    """Match what the member named against subjects that actually have rows.

    Refuses BY NAME when nothing matches, and lists what does exist. That is
    the difference between "I don't have a DAO repository with commits — I
    have 18-sana/Chain-Guard" and an empty chart, which reads as "nobody
    worked" rather than "you asked for something that isn't there".
    """
    return _resolve_value(
        "subject", spec.focus, spec, metric, org_id=org_id,
        workspace_id=workspace_id, days=days, viewer=viewer,
    )


def _resolve_filters(spec, metric, *, org_id, workspace_id, days, viewer=None, attrs=None):
    """Each grammar filter's raw value, matched to a value with rows.

    Same contract as ``focus``: "only Sana's" becomes the stored actor
    ``18-sana`` or a refusal naming who IS there -- never a filter on text
    nobody stored, which would draw an empty chart reading "no activity".
    A filter that cannot be verified (the listing failed) is DROPPED and the
    chart is still drawn, exactly as an unverifiable focus is.
    """
    out = []
    for dim, raw in spec.filters:
        value = _resolve_value(
            dim, raw, spec, metric, org_id=org_id,
            workspace_id=workspace_id, days=days, viewer=viewer, attrs=attrs,
        )
        if value is not None:
            out.append((dim, value))
    return tuple(out)


#: How a dimension is named back to the member in a refusal.
_DIM_NOUN = {"actor": "person", "state": "state"}

#: The two tokens the classifier sends for "not finished" / "finished",
#: whatever words the asker used (the prompt does the language).
_OPEN, _CLOSED = "open", "closed"

def _state_group(spec, wanted: str, raw: str, *, org_id, workspace_id, days,
                 viewer=None) -> query.AnyOf | None:
    """"open" -> every stored state the source calls unfinished; "closed" ->
    those it calls finished. Decided ONLY by the source's own state type
    (``store.state_types``): a state the source gave no type is in neither,
    and with no types at all the filter is refused naming the real states."""
    if wanted not in (_OPEN, _CLOSED):
        return None
    try:
        kinds = store.state_types(spec.metric, org_id=org_id, workspace_id=workspace_id,
                                  days=days, viewer=viewer)
    except ProviderError:
        logger.warning("insights: could not read state types of %s", spec.metric)
        return None

    want_finished = wanted == _CLOSED
    picked = [s for s, k in kinds.items()
              if k and (k.lower() in store.FINISHED_STATE_TYPES) == want_finished]
    return query.AnyOf(raw.strip().capitalize(), tuple(picked)) if picked else None


def _resolve_value(
    dim, raw, spec, metric, *, org_id, workspace_id, days, viewer=None, attrs=None,
) -> str | None:
    wanted = raw.strip().lower()
    try:
        if dim == "subject":
            values = store.list_subjects(
                spec.metric, org_id=org_id, workspace_id=workspace_id,
                days=days, viewer=viewer,
            )
        else:
            values = store.list_values(
                spec.metric, dim, org_id=org_id, workspace_id=workspace_id,
                days=days, viewer=viewer, attrs=attrs,
            )
    except ProviderError:
        # Cannot verify, so do not filter. A whole chart beats a wrong one.
        logger.warning("insights: could not list %s of %s", dim, spec.metric)
        return None

    if not values:
        return None

    exact = [v for v in values if v.lower() == wanted]
    if exact:
        return exact[0]
    if dim == "state":
        # "Open Linear issues": no tool stores "Open"; it means not finished.
        # Only after an exact match, so a team whose state IS "Open" gets it.
        group = _state_group(spec, wanted, raw, org_id=org_id, workspace_id=workspace_id,
                             days=days, viewer=viewer)
        if group is not None:
            return group

    # Compared with punctuation and spacing removed: a member types "chain
    # guard" or "chain-guard" for a repo stored as "18-sana/Chain-Guard", and
    # a match that fails on a hyphen is indistinguishable from no such repo.
    key = _squash(wanted)
    if not key:
        return None
    # Both directions: "DAO" is inside "18-sana/DAO", and someone may paste
    # the full "18-sana/Chain-Guard" for a subject stored bare.
    partial = [
        v for v in values
        if key in _squash(v) or (_squash(v) and _squash(v) in key)
    ]
    if len(partial) == 1:
        return partial[0]
    # The name is repeated back on purpose (see the docstring), but it is
    # model-extracted text, so it never carries a link back to the reader.
    named = enforce_link_provenance(raw, [], ())
    if len(partial) > 1:
        raise CannotChart(
            f"\"{named}\" matches more than one: "
            f"{_listed(partial)}. Which one?"
        )
    noun = _DIM_NOUN.get(dim) or _dim_label(metric, dim, attrs)
    raise CannotChart(
        f"**No {noun} called \"{named}\"**\n"
        f"{metric.label} in the last {days} days: {_listed(values)}."
    )


def _squash(value: str) -> str:
    """Letters and digits only, lowercased. `#rag-updates` -> `ragupdates`."""
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _listed(values: list[str], limit: int = 6) -> str:
    shown = values[:limit]
    rest = len(values) - len(shown)
    text = ", ".join(shown)
    return f"{text} and {rest} more" if rest > 0 else text


def _empty_caption(spec, title, *, org_id, workspace_id, days, metric, viewer=None) -> str:
    """Say WHICH kind of empty this is. There are three, and they need
    different actions from the member.

    "Nothing recorded yet" for all three sent people to wait for a sync that
    would never change anything -- the commonest real case is activity that
    exists just outside the window, where the fix is one word in the question.
    """
    if metric is None:
        return f"{title}. Nothing recorded for that yet."

    began = None
    if org_id:
        try:
            began = store.first_fact_at(
                metric.provider, org_id=org_id, workspace_id=workspace_id,
                viewer=viewer,
            )
        except ProviderError:
            began = None

    if began is None:
        # Nothing from this connector has EVER been counted in this scope.
        return (
            f"{title}. Nothing from {metric.provider.title()} has been counted "
            "in this space yet. If it was connected before charts existed, the "
            "next sync starts counting — there is nothing to fix."
        )

    # There ARE facts from this provider, just not of this kind in this window.
    wider = _has_older_rows(
        metric, org_id=org_id, workspace_id=workspace_id, viewer=viewer
    )
    if wider:
        return (
            f"{title}. Nothing in the last {days} days, but there IS older "
            f"activity — the earliest is {began.date().isoformat()}. Ask for "
            "it quarterly to widen the window."
        )
    return (
        f"{title}. No {metric.unit or 'activity'} recorded in the last "
        f"{days} days. Other {metric.provider.title()} activity has been "
        f"counted since {began.date().isoformat()}, so the connection is "
        "working — this particular thing simply has not happened."
    )


def _has_older_rows(
    metric, *, org_id: str, workspace_id: str | None, viewer: Viewer | None = None
) -> bool:
    """Whether this exact metric has rows beyond the widest chart window.

    Answers the question the member actually has -- "is it missing, or am I
    looking at the wrong period?" -- and it is the case that bit a real
    tenant: 23 pull requests existed, every one of them older than the
    recording window, and the chart said "nothing recorded yet".
    """
    try:
        points = store.run_metric(
            metric.key, org_id=org_id, workspace_id=workspace_id,
            period="quarter", days=max(scopes.WINDOW_DAYS.values()),
            viewer=viewer,
        )
    except (ProviderError, ValueError, KeyError):
        return False
    return bool(points)


def _caption(
    spec: ChartSpec, panel: dict, points: list, *,
    org_id: str = "", workspace_id: str | None = None, days: int = 0,
    metric=None, viewer: Viewer | None = None,
) -> str:
    title = panel["title"]
    if not points:
        return _empty_caption(
            spec, title, org_id=org_id, workspace_id=workspace_id,
            days=days, metric=metric, viewer=viewer,
        )
    if spec.group_by == "actor" and all(not p.get("group") for p in points):
        return (
            f"{title}. Editor names were not stored when these were first "
            "indexed, so this is a total rather than a breakdown by person. "
            "The next sync will start capturing who edited."
        )
    return title


def _grammar_caveat(metric, group_by, split_by, attrs=None) -> str:
    """The metric's caveat, plus the one a TAG breakdown adds: an item with
    two labels is in two bars, so the bars sum to more than the items. A
    chart whose bars over-add without saying so reads as a miscount."""
    notes = [metric.caveat] if metric.caveat else []
    for dim in (group_by, split_by):
        if query.is_tag(metric, dim, attrs):
            notes.append(
                f"An item with several {query.find_attr(metric, dim, attrs).label}s counts "
                f"under each, so the bars can add up to more than the total."
            )
            break
    if any(query.find_attr(metric, d, attrs) for d in (group_by, split_by) if d):
        notes.append(
            "Recorded from each item's next sync after this field was added; "
            "older items with no value show as Unknown."
        )
    return " ".join(notes)


def _dim_label(metric, dim: str, attrs=None) -> str:
    return query.dim_label(metric, dim, attrs)


def _ask_title(
    metric, group_by: str | None, *, split_by: str | None = None,
    measure: str | None = None, filters: tuple[tuple[str, str], ...] = (),
    attrs=None,
) -> str:
    """Says every slice applied. A split, a distinct count or a person filter
    that the title omits reads as the plain chart -- the same failure as a
    focus the title omits."""
    chosen = query.measures_for(metric, attrs).get(measure) if measure else None
    title = metric.label
    if chosen is not None and chosen.label:
        title = f"{chosen.label} — {metric.label.lower()}"
    if group_by:
        title += f" by {_dim_label(metric, group_by, attrs)}"
        if split_by:
            title += f" and {_dim_label(metric, split_by, attrs)}"
    for dim, value in filters:
        title += f" — {_dim_label(metric, dim, attrs)}: {value}"
    return title


def _run_spec(
    spec: ChartSpec,
    *,
    org_id: str,
    workspace_id: str | None,
    user_id: str,
    role: str,
    viewer: Viewer | None = None,
) -> tuple[dict, str]:
    try:
        metric = registry.get(spec.metric)
    except KeyError as exc:
        raise CannotChart("No such chart.") from exc

    if not scopes.may_see_metric(
        metric, role=role, workspace_id=workspace_id, org_id=org_id, user_id=user_id
    ):
        raise CannotChart("I can't chart that here.")

    days = scopes.WINDOW_DAYS.get(spec.period, scopes.WINDOW_DAYS["month"])
    group_by = spec.group_by
    chart = spec.chart

    # "commits in the DAO repo" narrows; it does not group. Resolved against
    # the subjects that ACTUALLY have rows, so an unmatched name is refused BY
    # NAME instead of quietly filtering to nothing -- an empty chart reads as
    # "no activity" when the truth is "no such repository".
    focus = None
    if spec.focus:
        focus = _resolve_focus(spec, metric, org_id=org_id,
                               workspace_id=workspace_id, days=days, viewer=viewer)
    split_by = spec.split_by if group_by else None
    measure = spec.measure
    # The recorded fields this scope actually has (attr_catalog), read ONCE
    # and handed to every step below -- validation, the query, refinement,
    # the hover rows, the title -- so they all agree on what exists. Only read
    # when the spec names something beyond the built-in columns.
    attrs = None
    if store._needs_attrs(group_by=group_by, split_by=split_by,
                          measure=measure, filters=spec.filters):
        attrs = attr_catalog.for_metric(
            metric, org_id=org_id, workspace_id=workspace_id, viewer=viewer,
        )
    filters = _resolve_filters(spec, metric, org_id=org_id, workspace_id=workspace_id,
                               days=days, viewer=viewer, attrs=attrs)
    try:
        query.validate(metric, group_by=group_by, split_by=split_by,
                       measure=measure, filters=filters, attrs=attrs)
    except ValueError as exc:
        # The resolver validated this already; a spec arriving another way
        # (a stored dict, a future caller) is refused the same way.
        raise CannotChart(f"I can't chart it that way. {exc}") from exc
    if chart == "pie" and group_by is None:
        # A pie needs groups to be shares OF something. Without one it is a
        # single full circle, which states nothing.
        chart = "line"
    period = spec.period
    points = store.run_metric(
        spec.metric,
        org_id=org_id,
        workspace_id=workspace_id,
        period=period,
        days=days,
        group_by=group_by,
        focus=focus,
        viewer=viewer,
        split_by=split_by,
        measure=measure,
        filters=filters,
        attrs=attrs,
    )

    # A period that puts EVERYTHING in one bucket draws as a single point --
    # a flat line, or a pie with one slice -- and reads as "no data" when the
    # data is fine and the bucket was too wide. Four commits on two days are
    # one bar at week AND at month. Step finer until the shape shows what
    # happened, at most twice: this is the same rows re-bucketed, never a
    # different question, and the caption says which period was used.
    refined = period
    while (
        len(_buckets(points)) <= 1
        and registry.FINER_PERIOD.get(refined)
        and refined != registry.FINER_PERIOD.get(refined)
    ):
        finer = registry.FINER_PERIOD[refined]
        finer_window = max(scopes.WINDOW_DAYS.get(finer, days), _span_days(points))
        try:
            candidate = store.run_metric(
                spec.metric, org_id=org_id, workspace_id=workspace_id,
                period=finer, days=finer_window,
                group_by=group_by, focus=focus, viewer=viewer,
                split_by=split_by, measure=measure, filters=filters, attrs=attrs,
            )
        except (ProviderError, ValueError):
            break
        refined = finer
        if len(_buckets(candidate)) > len(_buckets(points)):
            points, period, days = candidate, finer, finer_window
            if len(_buckets(points)) > 1:
                break

    # `days` stays the window that ACTUALLY produced these points, never the
    # refined period's default. Resetting it to the default silently emptied
    # the detail rows: July's commits are outside a 45-day daily window in
    # September, so the chart had four bars and nothing behind them.
    begun = store.first_fact_at(
        metric.provider, org_id=org_id, workspace_id=workspace_id, viewer=viewer
    )
    if group_by is None:
        # A trend shows its whole range, quiet periods at zero -- from when
        # this tool's data begins (or the window, if later), never before.
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=days)
        since = max(begun, window_start) if begun else None
        points = _fill_gaps(points, period, since, now)
    title = _ask_title(
        metric, group_by, split_by=split_by, measure=measure, filters=filters,
        attrs=attrs,
    )
    if focus:
        # In the title, because a filtered chart that looks unfiltered is the
        # same failure as charting the wrong thing.
        title = f"{title} — {focus}"

    chosen = query.measures_for(metric, attrs)[measure or query.default_measure(metric)]
    panel = {
        "id": (
            f"ask:{spec.metric}:{group_by or 'time'}:{split_by or '-'}:"
            f"{chosen.name}:{focus or 'all'}:"
            + ",".join(f"{d}={v}" for d, v in filters)
        ),
        "provider": metric.provider,
        "title": title,
        "focus": focus,
        "chart": chart,
        "group_by": group_by,
        # The second grouping: the chart draws "group · split" categories and
        # matches hover rows on BOTH fields.
        "split_by": split_by,
        "filters": [list(f) for f in filters],
        "unit": chosen.unit,
        "caveat": _grammar_caveat(metric, group_by, split_by, attrs),
        "points": [
            {"bucket": p.bucket, "group": p.group, "series": p.series, "value": p.value}
            for p in points
        ],
        # The rows the bars are made of. "4 commits" answers how many and
        # nothing else; which ones, by whom and where to read them are the
        # questions that follow immediately, and every column is already on
        # the counted row.
        "details": _details(
            spec, org_id=org_id, workspace_id=workspace_id, days=days, focus=focus,
            viewer=viewer, filters=filters, attrs=attrs,
        ),
        "measured_since": begun.isoformat() if begun else None,
    }
    return panel, period
