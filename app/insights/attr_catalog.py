"""Which recorded fields actually exist, read from the data itself.

The other half of ``fields.simple_fields``: the writers keep every simple
field a tool returned, and this works out -- per metric, per scope -- which of
them a chart can use, and how. Nothing is listed by hand, so a field becomes
chartable the day a tool starts sending it. ``registry.ATTRS`` survives only
as display HINTS (a nicer name, a unit) for fields we already know.

How a field is typed, from the rows in scope:

- a JSON array            -> ``tags``     (an item counts under each)
- a number                -> ``number``   (summable)
- text or true/false      -> ``category``, but ONLY when it actually groups:
  at most ``MAX_CATEGORY_DISTINCT`` values and not unique per item. A branch
  name on every PR, say, would draw one bar per PR -- a list, not a chart.

A field must appear on at least ``MIN_ROWS`` rows, and a key that is not a
bare identifier is ignored even though the writer already normalized it: the
key is spliced into SQL as a JSON key literal, so it is checked at read time
too. Viewer-filtered, so a field present only on items this person cannot
open is not even named to them.
"""

from __future__ import annotations

import logging

from ..core.exceptions import ProviderError
from ..db.connection import get_connection
from . import registry
from .fields import KEY_RE

logger = logging.getLogger(__name__)

MIN_ROWS = 2
MAX_CATEGORY_DISTINCT = 50
#: A text field with (nearly) one value per item is an identifier, not a
#: category.
MAX_UNIQUE_SHARE = 0.9
#: Per metric, so one verbose tool cannot flood the classifier's prompt.
MAX_PER_METRIC = 25
WINDOW_DAYS = 180


def _hint(provider: str, key: str) -> registry.Attr | None:
    return next((a for a in registry.ATTRS if a.provider == provider and a.key == key), None)


def _label(key: str) -> str:
    return key.replace("_", " ")


def discover_scope(
    *, org_id: str, workspace_id: str | None, viewer=None, days: int = WINDOW_DAYS,
) -> dict[tuple[str, str], tuple[registry.Attr, ...]]:
    """``{(provider, kind): attrs}`` for every fact kind in this scope.

    One query for the whole scope (the router calls this once per chart-shaped
    question). Raises ``ProviderError``; callers degrade to no attributes.
    """
    from .store import _scoped, _viewer_filter  # one spelling of each rule

    where = _scoped(
        " WHERE org_id = %(org_id)s"
        " AND occurred_at >= now() - make_interval(days => %(days)s)",
        workspace_id,
    )
    access, access_params = _viewer_filter(viewer)
    where += access
    # Unaliased on purpose: `_viewer_filter` names `activity_facts.provider`.
    sql = f"""
        SELECT provider, kind, e.key, jsonb_typeof(e.value) AS t,
               count(*) AS n,
               count(DISTINCT e.value) AS distinct_values,
               count(DISTINCT external_id) AS items
          FROM activity_facts
          CROSS JOIN LATERAL jsonb_each(activity_facts.attrs) e
          {where}
         GROUP BY 1, 2, 3, 4
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                sql, {"org_id": org_id, "workspace_id": workspace_id, "days": days,
                      **access_params},
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError("insights: could not read recorded fields", cause=exc) from exc

    # Per (provider, kind, key), the dominant JSON type and its counts.
    best: dict[tuple[str, str, str], tuple] = {}
    for provider, kind, key, jtype, n, distinct, items in rows:
        k = (provider, kind, key)
        if k not in best or n > best[k][1]:
            best[k] = (jtype, n, distinct, items)

    out: dict[tuple[str, str], list[registry.Attr]] = {}
    for (provider, kind, key), (jtype, n, distinct, items) in sorted(best.items()):
        if not KEY_RE.match(key or "") or n < MIN_ROWS:
            continue
        if jtype == "array":
            kind_of = "tags"
        elif jtype == "number":
            kind_of = "number"
        elif jtype in ("string", "boolean"):
            unique_share = distinct / max(items, 1)
            if distinct > MAX_CATEGORY_DISTINCT or (
                items >= 5 and unique_share > MAX_UNIQUE_SHARE
            ):
                continue
            kind_of = "category"
        else:
            continue
        hint = _hint(provider, key)
        if hint is not None and hint.type != kind_of:
            # The data disagrees with the hint (a tool changed shape): the
            # data wins, since the SQL has to match what is stored.
            hint = None
        out.setdefault((provider, kind), []).append(registry.Attr(
            key=key, provider=provider, kinds=(kind,), type=kind_of,
            label=hint.label if hint else _label(key),
            unit=(hint.unit if hint else "") or (_label(key) if kind_of == "number" else ""),
        ))
    return {k: tuple(v[:MAX_PER_METRIC]) for k, v in out.items()}


def for_metric(
    metric: registry.Metric, *, org_id: str, workspace_id: str | None, viewer=None,
) -> tuple[registry.Attr, ...]:
    """The fields one metric's rows carry in this scope. Never raises:
    a failed read costs the extra breakdowns, never the chart."""
    try:
        found = discover_scope(org_id=org_id, workspace_id=workspace_id, viewer=viewer)
    except ProviderError:
        logger.warning("insights: field discovery failed for %s", metric.key, exc_info=True)
        return ()
    return found.get((metric.provider, metric.kind), ())


def by_metric(found: dict[tuple[str, str], tuple]) -> dict[str, tuple[registry.Attr, ...]]:
    """``discover_scope`` output keyed by metric key, for the resolver."""
    return {
        m.key: found.get((m.provider, m.kind), ())
        for m in registry.METRICS.values()
    }
