"""The chart query grammar: what a question may ask of one registry metric.

A registry ``Metric`` fixes WHAT is counted (one provider, one ``kind``, its
caveat and its protections). This module fixes HOW that thing may be sliced,
so the model composes a chart instead of picking one finished chart from a
menu -- "pull requests merged by person AND repository, only Sana's" used to
refuse against rows that were all there, because no metric happened to be
that exact combination. It is Cube's / ThoughtSpot's pattern
(docs/plans/2026-09-30-open-ended-charts.md): the model emits a constrained
query, code validates and compiles it, and the model still never writes SQL
or produces a number.

Three slots, each a CLOSED set per metric:

- ``measure``: what each bar is. Counts may also count distinct people or
  distinct subjects; duration metrics may be averaged or maxed as well as the
  median. Every fragment is a constant here, never built from input.
- ``split_by``: a SECOND grouping, drawn as "group · split" categories. Only
  from the metric's own ``dims``.
- ``filters``: narrow to one person or one state. The VALUE is raw text the
  member typed and is resolved against values that actually have rows before
  it reaches SQL, where it is bound -- exactly how ``focus`` already works for
  subjects.

A protected metric (a suppression floor or ``owners_only``, i.e. survey
sentiment) gets NONE of this: a split multiplies buckets past the floor's
reasoning, a person filter points a sentiment chart at one colleague, and
"count distinct people" over survey rows is a headcount of respondents. Its
chart stays exactly the one the registry defines.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import registry

#: The dimensions a filter may name. ``subject`` is deliberately absent: it
#: already has its own slot (``ChartSpec.focus``) with its own resolution and
#: its own place in the title, and two ways to say one thing drift apart.
FILTER_DIMS = ("actor", "state")

#: A duration metric's fragments. Only valid for metrics whose ``value`` is
#: SECONDS and whose unit is days -- ``tests/test_insights_query.py`` pins
#: both, so a future value metric in another unit cannot silently inherit
#: "/ 86400".
_DURATION_MEASURES = {
    "average": ("avg(value) / 86400.0", "days (average)"),
    "longest": ("max(value) / 86400.0", "days (longest)"),
}

#: Plural nouns for a count of distinct subjects, per provider. Mirrors
#: ``registry.SUBJECT_LABELS``; a unit reading "subjects" names nothing.
_SUBJECT_PLURALS = {
    "notion": "pages",
    "google": "files",
    "slack": "channels",
    "linear": "teams",
    "github": "repositories",
}


@dataclass(frozen=True)
class Measure:
    """One way to turn the counted rows into a bar's height."""

    name: str
    #: ``None`` = the metric's own ``select``, so the default measure of every
    #: metric is byte-identical to what it charted before this grammar existed.
    select: str | None
    unit: str
    #: Shown in the title, e.g. "People", so a distinct count never reads as
    #: the plain count it replaced.
    label: str | None = None


def is_protected(metric: registry.Metric) -> bool:
    """A metric whose chart must stay exactly as the registry defines it."""
    return metric.min_group_count > 0 or metric.owners_only or bool(metric.series_by)


def is_duration(metric: registry.Metric) -> bool:
    return "value" in metric.select


def _attrs(metric: registry.Metric, *types: str) -> tuple[registry.Attr, ...]:
    """Declared attributes of this metric, none at all for a protected one."""
    if is_protected(metric):
        return ()
    return tuple(a for a in registry.attrs_for(metric) if a.type in types)


def measures_for(metric: registry.Metric) -> dict[str, Measure]:
    """Every measure this metric admits, the DEFAULT first."""
    default_name = "median" if is_duration(metric) else "count"
    out = {default_name: Measure(default_name, None, metric.unit)}
    if is_protected(metric):
        return out
    if is_duration(metric):
        for name, (select, unit) in _DURATION_MEASURES.items():
            out[name] = Measure(name, select, unit, label=name.title())
        return out
    # A number attribute can be summed or averaged: "estimate points
    # completed per team". The key is a declared identifier (registry.ATTRS),
    # so the fragment is still a constant, not caller text.
    for a in _attrs(metric, "number"):
        value = f"(activity_facts.attrs->>'{a.key}')::numeric"
        out[f"total_{a.key}"] = Measure(
            f"total_{a.key}", f"sum({value})", a.unit or a.label,
            label=f"Total {a.label}",
        )
        out[f"average_{a.key}"] = Measure(
            f"average_{a.key}", f"avg({value})", f"{a.unit or a.label} (average)",
            label=f"Average {a.label}",
        )
    if "actor" in metric.dims:
        out["people"] = Measure(
            "people", "count(DISTINCT actor)", "people", label="People",
        )
    if "subject" in metric.dims and metric.provider in _SUBJECT_PLURALS:
        plural = _SUBJECT_PLURALS[metric.provider]
        out["subjects"] = Measure(
            "subjects", "count(DISTINCT subject)", plural, label=plural.title(),
        )
    return out


def default_measure(metric: registry.Metric) -> str:
    return next(iter(measures_for(metric)))


def group_dims(metric: registry.Metric) -> tuple[str, ...]:
    """What a chart may be grouped by: the metric's own dims plus the
    category and tag attributes its rows carry."""
    return metric.dims + tuple(a.key for a in _attrs(metric, "category", "tags"))


def split_dims(metric: registry.Metric) -> tuple[str, ...]:
    """What a second grouping may be. Empty for a protected metric."""
    return () if is_protected(metric) else group_dims(metric)


def filter_dims(metric: registry.Metric) -> tuple[str, ...]:
    """What may be filtered to one value. Empty for a protected metric."""
    if is_protected(metric):
        return ()
    return tuple(d for d in FILTER_DIMS if d in metric.dims) + tuple(
        a.key for a in _attrs(metric, "category", "tags")
    )


def readable_attrs(metric: registry.Metric) -> tuple[registry.Attr, ...]:
    """Attributes a chart of this metric may read (and a hover may show)."""
    return _attrs(metric, "category", "tags", "number")


def is_tag(metric: registry.Metric, dim: str | None) -> bool:
    """A tag grouping counts a row once PER TAG, which the caveat must say."""
    found = registry.attr(metric, dim) if dim else None
    return bool(found and found.type == "tags")


def dim_sql(metric: registry.Metric, dim: str) -> tuple[str, str]:
    """``(expression, join)`` for grouping by ``dim``.

    A core dimension is a column. A category attribute is one JSON field. A
    TAG attribute fans a row out into one row per tag with a LEFT lateral
    join -- LEFT so an untagged row stays in the chart as an unnamed group
    rather than vanishing from the total. Every key reaching here is a
    declared identifier, never caller text.
    """
    if dim in registry.DIMENSIONS:
        return registry.DIMENSIONS[dim], ""
    found = registry.attr(metric, dim)
    if found is None or found.type == "number" or is_protected(metric):
        raise ValueError(f"unknown dimension {dim!r}")
    field = f"activity_facts.attrs->'{found.key}'"
    if found.type == "category":
        return f"(activity_facts.attrs->>'{found.key}')", ""
    alias = f"t_{found.key}"
    join = (
        f" LEFT JOIN LATERAL jsonb_array_elements_text("
        f"CASE WHEN jsonb_typeof({field}) = 'array' THEN {field} "
        f"ELSE '[]'::jsonb END) AS {alias}(v) ON true"
    )
    return f"{alias}.v", join


def filter_sql(metric: registry.Metric, dim: str, param: str) -> str:
    """`` AND <dim matches %(param)s>``. A tag filter keeps a row that
    carries the tag at all; a category or core filter needs equality."""
    if dim in FILTER_DIMS:
        return f" AND {registry.DIMENSIONS[dim]} = %({param})s"
    found = registry.attr(metric, dim)
    if found is None or found.type == "number":
        raise ValueError(f"unknown filter {dim!r}")
    if found.type == "tags":
        return f" AND activity_facts.attrs->'{found.key}' ? %({param})s"
    return f" AND activity_facts.attrs->>'{found.key}' = %({param})s"


def validate(
    metric: registry.Metric,
    *,
    group_by: str | None,
    split_by: str | None,
    measure: str | None,
    filters: tuple[tuple[str, str], ...] = (),
) -> None:
    """Raise ``ValueError`` naming the options, never correct the request.

    The store calls this as well as the resolver: the resolver's copy produces
    a refusal the member can act on, the store's copy is the guarantee that no
    call site (a pin, a test, a future route) can compile something the
    grammar does not admit.
    """
    if group_by is not None and group_by not in group_dims(metric):
        raise ValueError(
            f"{metric.label} cannot be grouped by {group_by!r}. "
            f"Options: {', '.join(group_dims(metric)) or 'none'}."
        )
    if measure is not None and measure not in measures_for(metric):
        raise ValueError(
            f"{metric.label} cannot be measured as {measure!r}. "
            f"Options: {', '.join(measures_for(metric))}."
        )
    if split_by is not None:
        if group_by is None:
            raise ValueError("a second grouping needs a first one")
        if split_by == group_by:
            raise ValueError("a chart cannot be split by the dimension it is grouped by")
        if split_by not in split_dims(metric):
            raise ValueError(
                f"{metric.label} cannot be split by {split_by!r}. "
                f"Options: {', '.join(split_dims(metric)) or 'none'}."
            )
    seen = set()
    for dim, value in filters:
        if dim not in filter_dims(metric):
            raise ValueError(
                f"{metric.label} cannot be narrowed by {dim!r}. "
                f"Options: {', '.join(filter_dims(metric)) or 'none'}."
            )
        if dim in seen:
            raise ValueError(f"two filters on {dim!r}")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"empty filter on {dim!r}")
        seen.add(dim)
