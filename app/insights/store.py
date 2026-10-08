"""Execute a registry metric as one scoped, parameterized aggregate.

The whole query is assembled from three sources and no others: a fixed
fragment from the registry, an identifier looked up in
``registry.DIMENSIONS``, and bound parameters. Nothing a caller typed ever
reaches the SQL text -- which matters more here than usual, because ``period``
and ``group_by`` are grammatically identifiers and so cannot be passed as %s.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from typing import TYPE_CHECKING

from ..core.exceptions import ProviderError
from ..db.connection import get_connection
from ..security.visibility import visibility_predicate
from ..sources.factory import ACL_CAPABLE
from . import query, registry

if TYPE_CHECKING:
    from ..vectorstore.base import Viewer


@dataclass(frozen=True)
class Point:
    """One bar, one dot.

    ``bucket`` is the time bucket; ``group`` is the series within it (an
    actor, a repo, a state) and is ``None`` when the metric was not grouped.
    """

    bucket: str
    group: str | None
    value: float
    #: The second dimension, when the metric declares one (``series_by``).
    #: A diverging bar is topic BY label, and one grouping cannot say that.
    series: str | None = None


def _scoped(sql_where: str, workspace_id: str | None) -> str:
    """The Workspace-within-a-Workspace predicate.

    ``workspace_id=None`` means org-wide and is NOT "any workspace": a space
    sees only its own rows and the org scope sees only org-wide ones. Written
    as a WHERE clause rather than a filter applied afterwards, so isolation
    never depends on the caller remembering to apply it.
    """
    return sql_where + (
        " AND workspace_id IS NULL" if workspace_id is None
        else " AND workspace_id = %(workspace_id)s"
    )


def _viewer_filter(viewer: "Viewer | None") -> tuple[str, dict]:
    """Drop facts about documents this viewer may not open. ``("", {})`` = no filter.

    A chart counts `activity_facts` rows and its hover lists them, so without
    this a count included a document the asker cannot open and the hover named
    its title. A fact from an ACL-capable provider is kept only when its
    DOCUMENT is visible, checked through the one predicate retrieval uses:
    `doc_changed` facts carry the document's own external id, and Linear's
    issue facts (keyed by identifier) carry the issue URL the document stores
    as `source_uri`. A fact with no visible document is hidden -- a deleted or
    never-indexed document has no sharing we can check, and hiding it fails
    closed. GitHub and Forms facts are untouched: GitHub embeds nothing (its
    boundary is the installation's repos) and Forms responses are never
    indexed.

    ponytail: the `OR` on url cannot use the unique index; fine at hundreds of
    facts per chart. Store the document key on the fact if charts get slow.
    """
    if viewer is None or viewer.is_unrestricted:
        return "", {}
    clause = f"""
           AND (activity_facts.provider <> ALL(%(acl_providers)s::text[]) OR EXISTS (
                SELECT 1 FROM documents d
                 WHERE d.org_id = activity_facts.org_id
                   AND d.workspace_id IS NOT DISTINCT FROM activity_facts.workspace_id
                   AND d.source_provider = activity_facts.provider
                   AND (d.source_external_id = activity_facts.external_id
                        OR d.source_uri = activity_facts.url)
                   AND {visibility_predicate("d", param="acl")}))
    """
    return clause, {"acl_providers": sorted(ACL_CAPABLE), "acl": viewer.acl()}


def _floored(inner: str, metric, *, has_series: bool) -> str:
    """Wrap a grouped query in the suppression floor, when the metric has one.

    In SQL rather than applied afterwards, because a small bucket must never
    leave the database: on a six-person team, "3 of 4 responses in Engineering
    are negative" identifies people, and a filter in the frontend is one
    forgotten call site away from rendering them.

    The subtlety is WHAT gets counted. A plain ``HAVING count(*) >= n`` counts
    each output row, which is right with one dimension and wrong with two: a
    diverging bar splits each topic across five sentiment labels, so a topic
    with twenty responses has four per label and would vanish entirely. The
    floor is a property of the TOPIC, so it is summed across the series with a
    window function -- which cannot appear in HAVING, hence the subquery.
    """
    if metric.min_group_count <= 0:
        return inner

    floor = int(metric.min_group_count)
    if not has_series:
        return f"SELECT * FROM ({inner}) f WHERE f.value >= {floor}"

    # `value` here is a per-(topic, label) count; the partition re-totals it
    # per topic so the floor applies to the whole bar.
    return f"""
        SELECT bucket, "group", series, value FROM (
            SELECT bucket, "group", series, value,
                   sum(value) OVER (PARTITION BY bucket, "group") AS group_total
              FROM ({inner}) f
        ) g
        WHERE g.group_total >= {floor}
    """


def _needs_attrs(group_by, split_by, measure, filters, dim=None) -> bool:
    """Does this request name anything beyond the built-in columns?"""
    named = [group_by, split_by, dim] + [d for d, _ in filters]
    return any(n and n not in registry.DIMENSIONS and n not in registry.DERIVED_DIMENSIONS
               for n in named) or bool(
        measure and (measure.startswith("total_") or measure.startswith("average_"))
    )


def _attrs_for(metric, attrs, *, org_id, workspace_id, viewer, **request):
    """The discovered fields, read only when the request names one.

    A field is usable only if it occurs in THIS scope's rows (and the viewer's),
    so the store discovers them itself rather than trusting a caller's list --
    no call site can compile SQL over a field that is not there.
    """
    if attrs is not None or not _needs_attrs(**request):
        return attrs
    from .attr_catalog import for_metric

    return for_metric(metric, org_id=org_id, workspace_id=workspace_id, viewer=viewer)


def _filter_clause(metric, filters: tuple[tuple[str, str], ...], attrs=None) -> tuple[str, dict]:
    """One `` AND ...`` per filter, from `query.filter_sql`. The column or JSON
    key and the parameter NAME both come from declarations, so the only
    caller text is the bound value."""
    sql, params = "", {}
    for dim, value in filters:
        if dim not in query.filter_dims(metric, attrs):
            raise ValueError(f"unknown filter {dim!r}")
        many = isinstance(value, query.AnyOf)
        sql += query.filter_sql(metric, dim, f"f_{dim}", attrs, many=many)
        params[f"f_{dim}"] = list(value.values) if many else value
    return sql, params


def run_metric(
    key: str,
    *,
    org_id: str,
    workspace_id: str | None,
    period: str,
    days: int = 90,
    group_by: str | None = None,
    focus: str | None = None,
    viewer: "Viewer | None" = None,
    split_by: str | None = None,
    measure: str | None = None,
    filters: tuple[tuple[str, str], ...] = (),
    attrs=None,
) -> list[Point]:
    """Count one registry metric in one scope over one window.

    ``viewer`` narrows the count to documents that person may open
    (`_viewer_filter`); every product call site passes one.

    ``split_by`` / ``measure`` / ``filters`` are the query grammar
    (`query.py`), re-validated here so no call site can compile what the
    grammar does not admit. Filter VALUES must already be resolved against
    real rows (`list_values`); they are bound, never spliced. With all three
    left at their defaults the SQL is exactly what it was before them.

    Raises ``KeyError`` for an unknown metric and ``ValueError`` for an unknown
    period or dimension -- never a sanitized fallback. A chart drawn from a
    quietly corrected request is a chart nobody asked for.
    """
    metric = registry.get(key)

    if period not in registry.PERIODS:
        raise ValueError(
            f"unknown period {period!r}; expected one of {registry.PERIODS}"
        )
    attrs = _attrs_for(
        metric, attrs, org_id=org_id, workspace_id=workspace_id, viewer=viewer,
        group_by=group_by, split_by=split_by, measure=measure, filters=filters,
    )
    query.validate(
        metric, group_by=group_by, split_by=split_by, measure=measure,
        filters=filters, attrs=attrs,
    )
    chosen = query.measures_for(metric, attrs)[measure or query.default_measure(metric)]
    select = chosen.select or metric.select

    # Every expression below comes from `query.dim_sql`: a whitelisted column,
    # or a JSON key DECLARED in `registry.ATTRS` -- never caller text. A tag
    # attribute also brings a lateral join (one row per tag).
    joins = ""
    column = None
    if group_by:
        column, join = query.dim_sql(metric, group_by, attrs)
        joins += join
    # Aliased because the suppression floor wraps this query and has to name
    # the columns. "group" is quoted -- it is a reserved word.
    selected = (
        f', {column}::text AS "group"' if column
        else ', NULL::text AS "group"'
    )
    grouped = f", {column}" if column else ""

    # The second dimension: the metric's own (fixed in the registry) or a
    # requested split. Never both -- a metric with `series_by` is protected
    # and admits no split (`query.is_protected`).
    second = metric.series_by or split_by
    series_column = None
    if second:
        if metric.series_by:
            series_column = registry.DIMENSIONS.get(second)
            if series_column is None:
                raise ValueError(f"{key} has unknown second dimension {second!r}")
        else:
            series_column, join = query.dim_sql(metric, second, attrs)
            joins += join
    selected += (
        f", {series_column}::text AS series" if series_column
        else ", NULL::text AS series"
    )
    grouped += f", {series_column}" if series_column else ""

    where = _scoped(
        """
         WHERE org_id = %(org_id)s
           AND provider = %(provider)s
           AND kind = %(kind)s
           AND occurred_at >= now() - make_interval(days => %(days)s)
        """,
        workspace_id,
    )
    # "commits in the DAO repo" is a FILTER, not a grouping. Without it the
    # question resolved to "commits by repository" and charted every repo --
    # a chart that answers a different question than the one asked, which is
    # worse than a refusal because it looks like an answer. `subject` holds a
    # VALUE (a repo, a channel, a team, a page title), so unlike `period` and
    # `group_by` it is bound as a parameter and never spliced.
    if focus is not None:
        where += " AND subject = %(focus)s"
    filter_sql, filter_params = _filter_clause(metric, filters, attrs)
    where += filter_sql
    access, access_params = _viewer_filter(viewer)
    where += access

    inner = f"""
        SELECT date_trunc('{period}', occurred_at) AS bucket{selected},
               {select} AS value
          FROM activity_facts{joins}
          {where}
         GROUP BY bucket{grouped}
    """

    sql = _floored(inner, metric, has_series=series_column is not None) + " ORDER BY bucket"

    params = {
        "org_id": org_id,
        "provider": metric.provider,
        "kind": metric.kind,
        "days": days,
        "workspace_id": workspace_id,
        "focus": focus,
        **filter_params,
        **access_params,
    }

    try:
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as exc:  # noqa: BLE001 - re-raised as our own type
        raise ProviderError(f"insights: metric {key} failed", cause=exc) from exc

    return [
        Point(
            bucket=row[0].isoformat(),
            group=row[1],
            series=row[2],
            value=float(row[3] or 0),
        )
        for row in rows
    ]


@dataclass(frozen=True)
class Fact:
    """One row behind a chart: what it was, who, when, and where to open it."""

    subject: str | None
    actor: str | None
    state: str | None
    occurred_at: str
    url: str | None
    #: Declared attributes (labels, priority, ...), so a hover over "bug" can
    #: list the rows that carry that label.
    attrs: dict = field(default_factory=dict)


#: How many rows travel with a chart. Enough to see what the bars are made of,
#: few enough that the payload stays a chart's companion rather than a table
#: someone has to scroll -- and the chart is still the answer.
MAX_DETAILS = 12


def list_facts(
    key: str,
    *,
    org_id: str,
    workspace_id: str | None,
    days: int,
    focus: str | None = None,
    limit: int = MAX_DETAILS,
    viewer: "Viewer | None" = None,
    filters: tuple[tuple[str, str], ...] = (),
    attrs=None,
) -> list[Fact]:
    """The newest rows this chart counted.

    A bar labelled "4" answers "how many" and nothing else -- which commits,
    by whom, when, and where to read them are the questions that follow
    immediately, and every one of those columns is already on the row being
    counted. Returned WITH the chart rather than behind a click, because a
    number nobody can trace is a number nobody trusts.
    """
    metric = registry.get(key)
    where = _scoped(
        """
         WHERE org_id = %(org_id)s
           AND provider = %(provider)s
           AND kind = %(kind)s
           AND occurred_at >= now() - make_interval(days => %(days)s)
        """,
        workspace_id,
    )
    if focus is not None:
        where += " AND subject = %(focus)s"
    # The hover lists what the bars counted, so it narrows exactly as they do:
    # Sana's PRs must not be shown behind a chart filtered to someone else.
    attrs = _attrs_for(
        metric, attrs, org_id=org_id, workspace_id=workspace_id, viewer=viewer,
        group_by=None, split_by=None, measure=None, filters=filters,
    )
    filter_sql, filter_params = _filter_clause(metric, filters, attrs)
    where += filter_sql
    access, access_params = _viewer_filter(viewer)
    where += access

    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT subject, actor, state, occurred_at, url, attrs
                  FROM activity_facts {where}
                 ORDER BY occurred_at DESC
                 LIMIT %(limit)s
                """,
                {"org_id": org_id, "provider": metric.provider, "kind": metric.kind,
                 "days": days, "workspace_id": workspace_id, "focus": focus,
                 "limit": max(1, min(int(limit), MAX_DETAILS)),
                 **filter_params, **access_params},
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"insights: details of {key} failed", cause=exc) from exc

    # Only DECLARED attributes travel with the hover: `attrs` may hold keys a
    # chart never reads, and the payload is not the place to find out.
    declared = {a.key for a in query.readable_attrs(metric, attrs)}
    return [
        Fact(
            subject=r[0], actor=r[1], state=r[2],
            occurred_at=r[3].isoformat() if r[3] else "",
            url=r[4],
            attrs=_with_progress(metric, {k: v for k, v in (r[5] or {}).items()
                                          if k in declared}, r[5] or {}),
        )
        for r in rows
    ]


def _with_progress(metric, shown: dict, stored: dict) -> dict:
    """Put the row's Open/Closed on the hover row when the metric can be
    grouped by it, decided exactly as the SQL decides (`DIMENSIONS`)."""
    if "progress" not in metric.dims:
        return shown
    kind = (stored.get("state_type") or "").strip().lower()
    if kind:
        shown = {**shown, "progress": "Closed" if kind in FINISHED_STATE_TYPES else "Open"}
    return shown


def list_subjects(
    key: str, *, org_id: str, workspace_id: str | None, days: int,
    viewer: "Viewer | None" = None,
) -> list[str]:
    """Every ``subject`` this metric actually has rows for, in this scope.

    What a member types is matched against THIS, never used as a filter
    directly -- so "the DAO repo" either resolves to a repository we have
    activity for or is refused by name. A filter built from unmatched text
    silently returns an empty chart, which reads as "no activity" when it
    means "no such thing".
    """
    return list_values(
        key, "subject", org_id=org_id, workspace_id=workspace_id, days=days,
        viewer=viewer,
    )


#: The state types a source reports for "the work is over". Linear's own
#: workflow categories -- an API contract, not words anyone typed.
FINISHED_STATE_TYPES = frozenset({"completed", "canceled"})


def state_types(
    key: str, *, org_id: str, workspace_id: str | None, days: int,
    viewer: "Viewer | None" = None,
) -> dict[str, str | None]:
    """Each state this metric has rows for, with the type its SOURCE gave it
    (``attrs.state_type``), or None where the source reports none. Same
    scope, window and viewer filter as ``list_values``."""
    metric = registry.get(key)
    where = _scoped(
        """
         WHERE org_id = %(org_id)s
           AND provider = %(provider)s
           AND kind = %(kind)s
           AND occurred_at >= now() - make_interval(days => %(days)s)
           AND state IS NOT NULL
        """,
        workspace_id,
    )
    access, access_params = _viewer_filter(viewer)
    where += access
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT state, MAX(attrs->>'state_type') FROM activity_facts "
                f"{where} GROUP BY state ORDER BY state",
                {"org_id": org_id, "provider": metric.provider, "kind": metric.kind,
                 "days": days, "workspace_id": workspace_id, **access_params},
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"insights: state types of {key} failed", cause=exc) from exc
    return {r[0]: r[1] for r in rows if r[0]}


def list_values(
    key: str, dim: str, *, org_id: str, workspace_id: str | None, days: int,
    viewer: "Viewer | None" = None,
    attrs=None,
) -> list[str]:
    """Every value of ``dim`` this metric has rows for, in this scope.

    The same role as ``list_subjects`` for the grammar's filters: "only
    Sana's" is resolved against the actors that actually appear, so an
    unmatched name is refused by name rather than filtering to nothing.
    Viewer-filtered because the refusal repeats the list back.
    """
    metric = registry.get(key)
    attrs = _attrs_for(
        metric, attrs, org_id=org_id, workspace_id=workspace_id, viewer=viewer,
        group_by=None, split_by=None, measure=None, filters=(), dim=dim,
    )
    if dim not in registry.DIMENSIONS and dim not in query.filter_dims(metric, attrs):
        raise ValueError(f"unknown dimension {dim!r}")
    column, joins = query.dim_sql(metric, dim, attrs)
    where = _scoped(
        f"""
         WHERE org_id = %(org_id)s
           AND provider = %(provider)s
           AND kind = %(kind)s
           AND occurred_at >= now() - make_interval(days => %(days)s)
           AND {column} IS NOT NULL
        """,
        workspace_id,
    )
    # A Drive subject is a FILE TITLE, and the refusal repeats the list back
    # ("What I do have: ..."), so an unfiltered list names withheld files.
    access, access_params = _viewer_filter(viewer)
    where += access
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT {column} FROM activity_facts{joins} {where} ORDER BY 1",
                {"org_id": org_id, "provider": metric.provider, "kind": metric.kind,
                 "days": days, "workspace_id": workspace_id, **access_params},
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"insights: {dim} values of {key} failed", cause=exc) from exc
    return [r[0] for r in rows if r[0]]


#: Names per field shown to the chart classifier. A bound, not a feature:
#: the most active ones, so a tool with hundreds of channels still fits.
MAX_NAMES_SHOWN = 15


def scope_names(
    *, org_id: str, workspace_id: str | None, days: int, viewer: "Viewer | None" = None,
    limit: int = MAX_NAMES_SHOWN,
) -> dict[tuple[str, str], dict[str, list[str]]]:
    """The real ``subject`` and ``actor`` values per (provider, kind), most
    active first: what the chart classifier may pick a focus or a person
    from, so "our team" is not mistaken for a team called "our team".

    One query for every metric, viewer-filtered like ``list_values`` because
    these names reach a prompt and, through a refusal, the asker.
    """
    access, access_params = _viewer_filter(viewer)
    where = _scoped(
        """
         WHERE org_id = %(org_id)s
           AND occurred_at >= now() - make_interval(days => %(days)s)
        """,
        workspace_id,
    ) + access
    sql = f"""
        WITH v AS (
            SELECT provider, kind, 'subject' AS dim, subject AS name, count(*) AS n
              FROM activity_facts {where} AND subject IS NOT NULL
             GROUP BY provider, kind, subject
            UNION ALL
            SELECT provider, kind, 'actor', actor, count(*)
              FROM activity_facts {where} AND actor IS NOT NULL
             GROUP BY provider, kind, actor
        )
        SELECT provider, kind, dim, name FROM (
            SELECT v.*, row_number() OVER (PARTITION BY provider, kind, dim
                                           ORDER BY n DESC, name) AS rk
              FROM v
        ) ranked
         WHERE rk <= %(limit)s
         ORDER BY provider, kind, dim, rk
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                sql, {"org_id": org_id, "workspace_id": workspace_id, "days": days,
                      "limit": limit, **access_params},
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("insights: could not list names in scope", cause=exc) from exc
    out: dict[tuple[str, str], dict[str, list[str]]] = {}
    for provider, kind, dim, name in rows:
        out.setdefault((provider, kind), {}).setdefault(dim, []).append(name)
    return out


def first_fact_at(
    provider: str, *, org_id: str, workspace_id: str | None,
    viewer: "Viewer | None" = None,
) -> datetime | None:
    """When measurement began for this provider in this scope.

    Facts only exist from the first sync after this feature deployed, and
    author names cannot be backfilled at all -- they were never captured. A
    chart whose axis silently starts on deploy day reads as if nobody worked
    before it, so the UI renders "measured since <this>". ``None`` means
    nothing has been recorded yet, which is a different statement from zero.
    """
    where = _scoped(
        " WHERE org_id = %(org_id)s AND provider = %(provider)s", workspace_id
    )
    access, access_params = _viewer_filter(viewer)
    where += access
    try:
        with get_connection() as conn:
            row = conn.execute(
                f"SELECT min(occurred_at) FROM activity_facts {where}",
                {"org_id": org_id, "provider": provider,
                 "workspace_id": workspace_id, **access_params},
            ).fetchone()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(
            f"insights: first_fact_at({provider}) failed", cause=exc
        ) from exc

    return row[0] if row else None
