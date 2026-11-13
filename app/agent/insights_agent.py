"""The Insights agent: counted charts, never RAG.

Numbers come from SQL over stored rows; the model only picks a registry
key and shape, never a total. A chart question that cannot be counted
returns the refusal with ``grounded=False``, never a retrieval answer.
"""

from __future__ import annotations

import re

import logging
from datetime import date, datetime
from collections.abc import Iterator

from ..core.answer_sources import SOURCE_NONE
from ..core.exceptions import ProviderError
from ..core.streaming import chunk_answer
from ..insights import describe
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
        # Every count, hover row and date below is narrowed to documents this
        # viewer may open (`store._viewer_filter`).
        del question  # Spec is already resolved; question is untrusted.
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
                                 viewer=viewer,
                                 uploads=_upload_scope(conversation_id, user_id))
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
        if not points and panel.get("read_note"):
            caption = f"{caption} {panel['read_note']}"
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


def _upload_scope(conversation_id: str | None, user_id: str | None):
    """This person's files in this chat, the only key to an upload's tables.
    Both or nothing."""
    if not conversation_id or not user_id:
        return None
    from ..doctables.store import UploadScope

    return UploadScope(conversation_id=conversation_id, user_id=user_id)


def _answer_table(spec: ChartSpec, *, org_id, workspace_id, viewer,
                  uploads=None) -> AgentResponse:
    """A chart of a table inside a document or an uploaded file."""
    try:
        panel, period = run_table_spec(
            spec, org_id=org_id, workspace_id=workspace_id, viewer=viewer,
            uploads=uploads,
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


def run_table_spec(spec: ChartSpec, *, org_id, workspace_id, viewer,
                   uploads=None) -> tuple[dict, str]:
    """Run a document-table chart. Raises ``CannotChart`` / ``ProviderError``.

    The table is looked up AGAIN with the viewer at run time (a spec may be a
    stored dict). No viewer means no table chart.
    """
    from ..doctables import store as table_store

    if viewer is None:
        raise CannotChart("I can't chart that here.")
    table = table_store.get_table(
        spec.table_id, org_id=org_id, workspace_id=workspace_id, viewer=viewer,
        uploads=uploads,
    )
    if table is None:
        # Deleted, re-indexed or not shared: indistinguishable on purpose, no title
        # repeated.
        raise CannotChart("That table is no longer available to chart.")

    # Column TYPES re-checked: a spec may be a stored dict, and a sum over text
    # is a SQL error.
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
    from ..insights.tables import is_period_filter

    for key, value in spec.filters:
        _need(key, ("date",) if is_period_filter(table, key, value) else ("category",), "filter")
    try:
        filters = _matched_filters(table, spec.filters)
    except ValueError as exc:
        raise CannotChart(f"I can't chart it that way. {exc}") from exc
    if spec.versus is not None:
        return _run_versus(spec, table, filters, org_id=org_id, workspace_id=workspace_id,
                           viewer=viewer, uploads=uploads)
    # A column of bare years is a yearly axis (format, not intent).
    period = spec.period
    group_col = table.column(spec.group_by) if spec.group_by else None
    samples = [str(x).strip() for x in (group_col or {}).get("samples", []) if str(x).strip()]
    if (group_col or {}).get("type") == "date" and samples and all(
            re.fullmatch(r"(?i)(fy\s?)?\d{4}", x) for x in samples):
        period = "year"
    try:
        points = table_store.run_table_query(
            table, measure=spec.measure or "count", value_key=spec.value,
            group_key=spec.group_by, split_key=spec.split_by, period=period,
            filters=filters,
        )
    except ValueError as exc:
        raise CannotChart(f"I can't chart it that way. {exc}") from exc

    def name(key):
        col = table.column(key) if key else None
        return col["name"] if col else key

    value_col = table.column(spec.value) if spec.value else None
    measure = spec.measure or "count"
    title = describe.table_title(
        measure, value_col, name(spec.group_by) if spec.group_by else None,
        name(spec.split_by) if spec.split_by else None,
    )
    for key, value in filters:
        title += f" — {name(key)}: {_shown(value)}"
    title += f" — {table.name}"
    if table.origin == "text":
        # In the title: figures read from prose must not look like a real table's.
        title += " (taken from text)"

    group_col = table.column(spec.group_by) if spec.group_by else None
    by_date = bool(group_col and group_col.get("type") == "date")
    source = (f"From \"{table.document_title}\", the file you uploaded to this chat."
              if table.is_upload else f"From the table in \"{table.document_title}\".")
    notes = [source] + list(table.notes)
    if value_col and value_col.get("unparsed"):
        notes.append(f"{value_col['unparsed']} {name(spec.value)} cells were not "
                     "numbers and are left out.")
    if table.truncated:
        notes.append("The table was longer than we keep; later rows are not counted.")

    unit = describe.table_unit(measure, value_col)
    try:
        rows_used = table_store.count_rows(
            table, filters=filters,
            value_key=spec.value if measure != "count" else None,
        )
    except ProviderError:
        rows_used = None
    explain = describe.table_explain(
        measure, value_col,
        group=name(spec.group_by) if spec.group_by else None,
        split=name(spec.split_by) if spec.split_by else None,
        period=period, by_date=by_date, chart=spec.chart,
        rows=rows_used if rows_used is not None else table.row_count,
        total=sum(p.value for p in points), unit=unit,
    )
    panel = {
        "id": f"table:{table.id}:{spec.group_by or '-'}:{spec.split_by or '-'}:"
              f"{measure}:{spec.value or '-'}:"
              + ",".join(f"{k}={_shown(v)}" for k, v in filters),
        "provider": table.provider,
        "title": title,
        "focus": None,
        "chart": spec.chart,
        # A DATE breakdown is the time axis and the split the category; otherwise
        # the breakdown is the category axis.
        "group_by": (spec.split_by if by_date else spec.group_by),
        "split_by": (None if by_date else spec.split_by),
        "filters": [[k, _shown(v)] for k, v in filters],
        "unit": unit,
        "measure": measure,
        "explain": explain,
        "caveat": " ".join(notes),
        "points": [
            {"bucket": p.bucket or "", "group": p.group, "series": p.series,
             "value": p.value}
            for p in points
        ],
        "details": _table_details(table, filters, date_key=spec.group_by if by_date else None),
        "measured_since": None,
        "table": {"id": table.id, "name": table.name, "document": table.document_title,
                  "origin": table.origin, "upload": table.is_upload},
    }
    return panel, period


def _matched_filters(table, raw_filters) -> tuple:
    """Typed filter values -> values the rows hold (one, or several kept). A
    period on a date column ("2026-12") becomes the days it covers."""
    from ..doctables.store import period_span
    from ..insights.tables import is_period_filter

    out = []
    for key, raw in raw_filters:
        if is_period_filter(table, key, raw):
            span = period_span(raw)
            if span is None:
                raise ValueError(f"{raw!r} is not a period")
            out.append((key, span))
        elif isinstance(raw, tuple):
            out.append((key, tuple(dict.fromkeys(_match_table_value(table, key, r) for r in raw))))
        else:
            out.append((key, _match_table_value(table, key, raw)))
    return tuple(out)


def _run_versus(spec: ChartSpec, first, first_filters, *, org_id, workspace_id, viewer,
                uploads=None) -> tuple[dict, str]:
    """Two tables' figures per the same names, as one grouped bar chart.

    The second table is re-fetched with the viewer too. Names match ignoring
    case and spaces; a name its column holds with no row is 0, one only the
    other table has is left out and said."""
    from ..doctables import store as table_store

    versus = spec.versus
    second = table_store.get_table(versus.table_id, org_id=org_id, workspace_id=workspace_id,
                                   viewer=viewer, uploads=uploads)
    if second is None:
        raise CannotChart("That table is no longer available to chart.")
    for tbl, key in ((first, spec.group_by), (second, versus.group_by)):
        if (tbl.column(key) or {}).get("type") != "category":
            raise CannotChart("I can't chart it that way: that column can't be the breakdown.")
    for tbl, measure, value in ((first, spec.measure or "sum", spec.value),
                                (second, versus.measure, versus.value)):
        if measure != "count" and (tbl.column(value) or {}).get("type") != "number":
            raise CannotChart("I can't chart it that way: there is no number column to add up.")
    second_filters = _matched_filters(second, versus.filters)

    def side(tbl, measure, value, group, filters):
        points = table_store.run_table_query(
            tbl, measure=measure, value_key=value if measure != "count" else None,
            group_key=group, split_key=None, period="month", filters=filters)
        out = {str(p.group): p.value for p in points if p.group is not None}
        if measure in ("count", "sum"):
            for name in table_store.list_values(tbl, group):
                out.setdefault(str(name), 0.0)
        return out

    try:
        a = side(first, spec.measure or "sum", spec.value, spec.group_by, first_filters)
        b = side(second, versus.measure, versus.value, versus.group_by, second_filters)
    except ValueError as exc:
        raise CannotChart(f"I can't chart it that way. {exc}") from exc
    key = lambda n: " ".join(n.lower().split())  # noqa: E731
    b_by = {key(n): v for n, v in b.items()}
    shared = [n for n in a if key(n) in b_by]
    if not shared:
        raise CannotChart(
            f"\"{first.name}\" and \"{second.name}\" share no names to compare.")
    a_keys = {key(n) for n in a}
    left_out = sorted([n for n in a if key(n) not in b_by] + [n for n in b if key(n) not in a_keys])

    def name_of(tbl, col):
        c = tbl.column(col) if col else None
        return c["name"] if c else "rows"

    a_label = name_of(first, spec.value) if (spec.measure or "sum") != "count" else f"Rows in {first.name}"
    b_label = name_of(second, versus.value) if versus.measure != "count" else f"Rows in {second.name}"
    if a_label == b_label:
        a_label, b_label = f"{a_label} ({first.name})", f"{b_label} ({second.name})"
    a_unit = describe.table_unit(spec.measure or "sum", first.column(spec.value) if spec.value else None)
    b_unit = describe.table_unit(versus.measure, second.column(versus.value) if versus.value else None)
    points = []
    for n in shared:
        points.append({"bucket": "", "group": n, "series": a_label, "value": a[n]})
        points.append({"bucket": "", "group": n, "series": b_label, "value": b_by[key(n)]})
    title = f"{a_label} vs {b_label} by {name_of(first, spec.group_by)}"
    for tbl, filters in ((first, first_filters), (second, second_filters)):
        for k, v in filters:
            title += f" — {name_of(tbl, k)}: {_shown(v)}"
    notes = [f"From the tables in \"{first.document_title}\" and \"{second.document_title}\"."]
    notes += [f"Names matched across the two tables; only in one of them: {_listed(left_out)}."
              ] if left_out else []
    panel = {
        "id": f"table:{first.id}:vs:{second.id}:{spec.group_by}:{versus.group_by}",
        "provider": first.provider,
        "title": title,
        "focus": None,
        "chart": "bar",
        "group_by": spec.group_by,
        "split_by": "measure",
        "filters": [[k, _shown(v)] for k, v in first_filters],
        "unit": a_unit if a_unit == b_unit else "",
        "series_units": {a_label: a_unit, b_label: b_unit},
        "explain": (f"Each pair of bars is one {name_of(first, spec.group_by).lower()}: "
                    f"{a_label} from \"{first.name}\" beside {b_label} from \"{second.name}\"."),
        "caveat": " ".join(notes),
        "points": points,
        "details": [],
        "measured_since": None,
        "table": {"id": first.id, "name": first.name, "document": first.document_title,
                  "origin": first.origin, "upload": first.is_upload},
    }
    return panel, spec.period


def _shown(value) -> str:
    """A filter value as written: several kept values joined, a period by its days."""
    from ..doctables.store import DateSpan

    if isinstance(value, DateSpan):
        last = date.fromordinal(value.end.toordinal() - 1)
        return f"{value.start.isoformat()} to {last.isoformat()}"
    return ", ".join(value) if isinstance(value, tuple) else str(value)


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
            # A figure read from prose shows its sentence, so a reader can check it.
            shown = f"“{quote}”"
        row = {
            "subject": shown[:300],
            "url": table.source_uri,
            # By column key, so a hover matches its bar (toOption.ts `fieldOf`).
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
    """Fill facts from the index for this scope, then re-run the metric, so
    Ask need not wait for the next ingest. No-op for GitHub (no documents)."""
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
    """Never fatal: a chart without its rows is still a chart."""
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


#: A filled time axis longer than this is left as is (mostly-empty noise).
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

    Only from ``since`` ("measured since"): a period before data begins is
    unknown, not zero. Per series, time charts only.
    """
    from datetime import datetime

    if not points or since is None:
        return points
    # Truncate in the database's zone, or a filled week sits hours off a real one.
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


#: Fewest periods a trend is drawn with before stepping to a finer period.
MIN_TREND_BUCKETS = 3


def _sparse_note(points, period: str, unit: str, group_by) -> str | None:
    """Say plainly when a trend has activity in one period only, so it does
    not read as a failed chart."""
    if group_by is not None:
        return None
    active = [p for p in points if p.value]
    if len(active) != 1 or len(_buckets(points)) < 2:
        return None
    total = active[0].value
    shown = int(total) if float(total).is_integer() else round(total, 2)
    return f"All activity so far ({shown} {unit}) is in one {period}."


def _span_days(points) -> int:
    """How far apart the found rows are. A finer period has a shorter default
    window, which can drop the very rows being shown."""
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
    """Match a named subject against subjects with rows. Refuses BY NAME
    listing what exists: an empty chart would read as "nobody worked"."""
    return _resolve_value(
        "subject", spec.focus, spec, metric, org_id=org_id,
        workspace_id=workspace_id, days=days, viewer=viewer,
    )


def _resolve_filters(spec, metric, *, org_id, workspace_id, days, viewer=None, attrs=None):
    """Each filter's raw value, matched to a value with rows, or a refusal
    naming who IS there. An unverifiable filter (listing failed) is dropped
    and the chart still drawn."""
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

#: The classifier's two tokens for "not finished" / "finished".
_OPEN, _CLOSED = "open", "closed"

def _state_group(spec, wanted: str, raw: str, *, org_id, workspace_id, days,
                 viewer=None) -> query.AnyOf | None:
    """"open"/"closed" -> stored states the source's own state type calls
    unfinished/finished (``store.state_types``). Untyped states are in neither;
    with no types the filter is refused naming the real states."""
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
        # Cannot verify, so do not filter: a whole chart beats a wrong one.
        logger.warning("insights: could not list %s of %s", dim, spec.metric)
        return None

    if not values:
        return None

    exact = [v for v in values if v.lower() == wanted]
    if exact:
        return exact[0]
    if dim == "state":
        # "Open" means not finished, but only after an exact state match.
        group = _state_group(spec, wanted, raw, org_id=org_id, workspace_id=workspace_id,
                             days=days, viewer=viewer)
        if group is not None:
            return group

    # Compared without punctuation or spacing: "chain guard" matches
    # "18-sana/Chain-Guard".
    key = _squash(wanted)
    if not key:
        return None
    # Both directions: a bare name or the full owner/name.
    partial = [
        v for v in values
        if key in _squash(v) or (_squash(v) and _squash(v) in key)
    ]
    if len(partial) == 1:
        return partial[0]
    # The name is model-extracted, so it never carries a link.
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
    """Say WHICH kind of empty this is: each needs a different action (often
    the activity is just outside the window)."""
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
    found = _github_pull_diagnosis(
        metric, org_id=org_id, workspace_id=workspace_id,
        focus=getattr(spec, "focus", None),
    )
    if found:
        return f"{title}. {found}"
    return (
        f"{title}. No {metric.unit or 'activity'} recorded in the last "
        f"{days} days. Other {metric.provider.title()} activity has been "
        f"counted since {began.date().isoformat()}, so the connection is "
        "working — this particular thing simply has not happened."
    )


#: Repositories probed when a pull-request chart is empty (a bound).
_PROBE_REPOS = 10


def _checked_repos(repos, total: int) -> str:
    """" Repositories checked: acme/api and 2 private repositories." Public
    repos are named so the answer's scope is clear; private ones only counted."""
    public = [r.full_name for r in repos if getattr(r, "private", None) is False]
    private = len(repos) - len(public)
    parts = public + ([f"{private} private repositor{'y' if private == 1 else 'ies'}"]
                      if private else [])
    if not parts:
        return ""
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    more = f" (the first {len(repos)} of {total})" if total > len(repos) else ""
    return f" Repositories checked{more}: {listed}."


def _diagnosis_repos(repos, focus: str | None) -> tuple[list, int, bool]:
    """Repos an empty pull-request chart asks GitHub about: the named one if
    this space reads it, else the first ``_PROBE_REPOS``. The bool: named found."""
    if not focus:
        return repos[:_PROBE_REPOS], len(repos), True
    key = _squash(focus)
    matched = [
        repo for repo in repos
        if key and (
            key in _squash(getattr(repo, "full_name", "") or "")
            or _squash(getattr(repo, "full_name", "") or "") in key
        )
    ]
    if not matched:
        return repos[:_PROBE_REPOS], len(repos), False
    return matched[:_PROBE_REPOS], len(matched), True


def _github_pull_diagnosis(
    metric, *, org_id: str, workspace_id: str | None, focus: str | None = None,
) -> str | None:
    """Why a GitHub PULL REQUEST chart is empty, asked of GitHub itself.

    Commits and pull requests are separate permissions. Asks for the newest
    pull request of the charted kind and names the cause with its date.
    Private repos are counted, never named. None when it cannot tell.
    Never raises.
    """
    if metric is None or metric.provider != "github" or not metric.kind.startswith("pr_"):
        return None
    from datetime import datetime, timedelta, timezone

    from ..insights.github_facts import KIND_MERGED, WINDOW_DAYS as read_back

    merged = metric.kind == KIND_MERGED
    try:
        from ..githublive import build_github_reader

        reader = build_github_reader(org_id, workspace_id)
        every = list(reader.list_repos())
    except Exception:  # noqa: BLE001 - see docstring
        return None
    repos, total, named_here = _diagnosis_repos(every, focus)
    if not repos:
        return None
    checked = _checked_repos(repos, total)
    if focus and not named_here:
        named = enforce_link_provenance(str(focus).strip(), [], ())
        checked = (
            f" \"{named}\" is not among the repositories this space reads."
            + checked
        )
    unreadable, any_pull, newest = 0, False, None
    for repo in repos:
        try:
            page = reader.list_pull_requests(
                repo.full_name, limit=1, state="merged" if merged else "all")
            if merged and not page.items:
                any_pull = any_pull or bool(
                    reader.list_pull_requests(repo.full_name, limit=1).items)
        except Exception:  # noqa: BLE001
            unreadable += 1
            continue
        for pull in page.items:
            any_pull = True
            when = pull.merged_at if merged else pull.created_at
            if when and (newest is None or when > newest):
                newest = when
    if unreadable == len(repos):
        return ("GitHub would not let Handbook read pull requests in this space's "
                "repositories, though commits are counted. Pull requests are a "
                "separate GitHub permission: an admin can give the Handbook GitHub "
                "App \"Pull requests: Read\" access; they are read the next time a "
                "GitHub chart is asked." + checked)
    if newest is None:
        if any_pull and merged:
            return ("This space's repositories have pull requests on GitHub, but "
                    "none has been merged yet. Ask for pull requests raised instead." + checked)
        if not any_pull:
            return ("This space's repositories have no pull requests on GitHub, so "
                    "there is nothing to count. Work may be pushed straight to the "
                    "main branch: try commits per week instead." + checked)
        return None
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=timezone.utc)
    what = "merged pull request" if merged else "pull request"
    day = newest.date().isoformat()
    if newest < datetime.now(timezone.utc) - timedelta(days=read_back):
        return (f"The most recent {what} on GitHub is from {day}, older than the "
                f"{read_back} days of GitHub activity Handbook reads, so there is "
                "nothing in range to count." + checked)
    return (f"GitHub shows a {what} on {day}, but it is not in what Handbook has read "
            "yet. Ask again in a moment; if it is still missing, reading GitHub is "
            "failing and the server log says why." + checked)


def _read_github_now(metric, *, org_id: str, workspace_id: str | None) -> str | None:
    """Read GitHub before counting a GitHub chart; a note when the read did not
    finish or failed, None when current. Never raises."""
    if metric is None or metric.provider != "github" or not org_id:
        return None
    try:
        from ..insights.github_facts import refresh_for_chart

        status = refresh_for_chart(org_id, workspace_id)
    except Exception:  # noqa: BLE001
        logger.warning("insights: on-demand GitHub read failed", exc_info=True)
        status = "failed"
    if status == "reading":
        return ("GitHub is still being read, so this may miss the last few minutes "
                "of activity. Ask again in a moment for the full picture.")
    if status == "failed":
        return "GitHub could not be read just now, so this uses what was read last."
    return None


def _has_older_rows(
    metric, *, org_id: str, workspace_id: str | None, viewer: Viewer | None = None
) -> bool:
    """Whether this metric has rows beyond the widest chart window ("missing,
    or wrong period?")."""
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
        blank = registry.BLANK_ACTOR.get(getattr(metric, "provider", ""))
        if blank:
            # The tool's person field is empty on every item: say so in its terms.
            return (f"{title}. None of these has an "
                    f"{registry.actor_label(metric.provider)} in "
                    f"{metric.provider.title()}, so all of them are {blank}.")
        return (
            f"{title}. Editor names were not stored when these were first "
            "indexed, so this is a total rather than a breakdown by person. "
            "The next sync will start capturing who edited."
        )
    return title


def _grammar_caveat(metric, group_by, split_by, attrs=None) -> str:
    """The metric's caveat, plus a TAG breakdown's: an item with two labels is
    in two bars, so the bars over-add."""
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
    """Says every slice applied (split, distinct count, person filter) so the
    chart never reads as the plain one."""
    chosen = query.measures_for(metric, attrs).get(measure) if measure else None
    title = metric.label
    # A non-default measure says what it is, never a bare "People".
    what = describe.activity_measure(metric, chosen)
    if what:
        title = f"{metric.label}: {what}"
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

    # GitHub is read when the chart is ASKED, so this morning's merge counts.
    read_note = _read_github_now(metric, org_id=org_id, workspace_id=workspace_id)
    # The asker's own range, else the period's default window.
    days = spec.days or scopes.WINDOW_DAYS.get(spec.period, scopes.WINDOW_DAYS["month"])
    group_by = spec.group_by
    chart = spec.chart

    # A focus narrows, not groups. Resolved against subjects with rows, so an
    # unknown name is refused BY NAME rather than drawn empty.
    focus = None
    if spec.focus:
        focus = _resolve_focus(spec, metric, org_id=org_id,
                               workspace_id=workspace_id, days=days, viewer=viewer)
    split_by = spec.split_by if group_by else None
    measure = spec.measure
    # The scope's recorded fields, read ONCE so every step agrees on what
    # exists; only when the spec names something beyond built-in columns.
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
        # Validated by the resolver already; another route is refused the same way.
        raise CannotChart(f"I can't chart it that way. {exc}") from exc
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

    # Everything in one bucket reads as "no data": step finer, at most twice
    # (same rows re-bucketed), and the caption names the period.
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

    # `days` stays the window that produced these points: the refined period's
    # default would empty the detail rows.
    begun = store.first_fact_at(
        metric.provider, org_id=org_id, workspace_id=workspace_id, viewer=viewer
    )
    stepped = None
    if group_by is None:
        # A trend shows its whole range from when data begins, quiet periods at zero.
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        window_start = now - timedelta(days=days)
        since = max(begun, window_start) if begun else None
        points = _fill_gaps(points, period, since, now)
        asked_period = period
        # Too few periods on the drawn axis to be a trend: step finer and say so.
        # Only with activity: stepping an empty chart shrank its window.
        while (any(p.value for p in points) and len(_buckets(points)) < MIN_TREND_BUCKETS
               and registry.FINER_PERIOD.get(period)):
            finer = registry.FINER_PERIOD[period]
            span = (now - begun).days + 1 if begun else 0
            window = max(scopes.WINDOW_DAYS.get(finer, days), min(span, days))
            try:
                candidate = store.run_metric(
                    spec.metric, org_id=org_id, workspace_id=workspace_id,
                    period=finer, days=window, group_by=None, focus=focus,
                    viewer=viewer, measure=measure, filters=filters, attrs=attrs,
                )
            except (ProviderError, ValueError):
                break
            start = max(begun, now - timedelta(days=window)) if begun else None
            candidate = _fill_gaps(candidate, finer, start, now)
            if len(_buckets(candidate)) > MAX_FILLED_BUCKETS:
                break
            points, period, days = candidate, finer, window
        if period != asked_period:
            stepped = (f"Shown per {period}: this tool's data begins "
                       f"{begun:%-d %b %Y}, too recent for a {asked_period}ly trend."
                       if begun else f"Shown per {period}.")
    title = _ask_title(
        metric, group_by, split_by=split_by, measure=measure, filters=filters,
        attrs=attrs,
    )
    if focus:
        # In the title: a filtered chart that looks unfiltered charts the wrong thing.
        title = f"{title} — {focus}"
    if spec.days:
        # The range asked for is said in the title.
        title = f"{title} — last {spec.days} days"

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
        # Second grouping: categories are "group · split"; hover matches both.
        "split_by": split_by,
        "filters": [list(f) for f in filters],
        "unit": chosen.unit,
        "explain": describe.activity_explain(
            metric, chosen,
            group=_dim_label(metric, group_by, attrs) if group_by else None,
            split=_dim_label(metric, split_by, attrs) if split_by else None,
            period=period, chart=chart,
        ),
        "read_note": read_note,
        "caveat": " ".join(n for n in (
            read_note, stepped, _sparse_note(points, period, chosen.unit, group_by),
            _grammar_caveat(metric, group_by, split_by, attrs)) if n),
        "points": [
            {"bucket": p.bucket, "group": p.group, "series": p.series, "value": p.value}
            for p in points
        ],
        # The rows behind the bars: which ones, by whom, and where to read them.
        "details": _details(
            spec, org_id=org_id, workspace_id=workspace_id, days=days, focus=focus,
            viewer=viewer, filters=filters, attrs=attrs,
        ),
        "measured_since": begun.isoformat() if begun else None,
        # What an EMPTY value means in this tool ("Unassigned"), not "Unknown".
        "blank_labels": _blank_labels(metric),
    }
    if spec.left_out:
        words = enforce_link_provenance(spec.left_out, [], ())
        panel["caveat"] = (
            f"A chart shows at most two breakdowns, so \"{words}\" was left out. "
            + panel["caveat"]
        ).strip()
    return panel, period


def _blank_labels(metric) -> dict:
    blank = registry.BLANK_ACTOR.get(metric.provider)
    return {"actor": blank} if blank else {}
