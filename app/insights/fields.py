"""Keep every SIMPLE field a source already returned, for charts.

Step 2 of docs/plans/2026-09-30-open-ended-charts.md, done properly. It first
shipped as six hand-picked keys (`registry.ATTRS`), which left the original
problem in place: a field nobody listed -- a PR's milestone, a Linear cycle --
was thrown away at sync and could never be charted. Now the whole payload a
facts writer already holds goes through ``simple_fields`` and what survives is
stored in ``activity_facts.attrs``; ``attr_catalog.discover`` then reads which
fields actually exist, so a field becomes chartable the day a tool sends it.

"Simple" is the whole point, and each rule is a size or privacy bound, not a
taste (the "won't it clutter the database?" question):

- **short values only** -- text up to ``MAX_TEXT`` characters, numbers,
  booleans, and short lists of names. A PR body, an issue description or a
  comment is content, not a category, and is the bulk of any payload;
- **no ids, links or timestamps** -- unique per item, so they can never group
  anything (``occurred_at`` already carries the one date a chart needs);
- **no email addresses**, anywhere -- a person is already ``actor``;
- **a nested object becomes its name** (``milestone`` -> its title, ``user`` ->
  its login), never the object: profiles and repository blocks repeat on every
  row;
- **at most ``MAX_FIELDS`` keys** per item, so a tool that starts returning
  more cannot grow a row without bound.

Keys are normalized to ``^[a-z][a-z0-9_]{0,39}$`` here, at WRITE time, and
checked against the same pattern at READ time (``KEY_RE``) -- that is what
lets an attribute key be used as a JSON key literal in SQL.
"""

from __future__ import annotations

import re

MAX_TEXT = 200
MAX_FIELDS = 40
MAX_LIST = 20

KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

#: Content, not categories. Dropped by NAME because a short body still is one.
_CONTENT_KEYS = frozenset({
    "body", "description", "text", "title", "content", "comment", "comments_text",
    "summary", "message", "patch", "diff",
})

#: Real `activity_facts` columns. An attribute with one of these names would
#: be ambiguous with the column a chart already groups by.
_RESERVED = frozenset({
    "actor", "subject", "state", "provider", "kind", "value", "external_id",
    "occurred_at", "org_id", "workspace_id", "actor_key", "attrs",
})

#: The name a nested object is reduced to, first match wins.
_NAME_KEYS = ("name", "title", "login", "ref", "key", "label")

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def normalize_key(key: str) -> str | None:
    """``projectMilestone`` -> ``project_milestone``; None if it cannot be one."""
    if not isinstance(key, str):
        return None
    snake = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", key).lower()
    snake = re.sub(r"[^a-z0-9_]+", "_", snake).strip("_")
    return snake if KEY_RE.match(snake or "") else None


def _dropped_key(key: str) -> bool:
    return (
        key in _CONTENT_KEYS
        or key == "id" or key.endswith("_id") or key.endswith("_ids")
        or key in ("number", "identifier", "sha", "node_id", "url", "uri", "href")
        or key.endswith("_url") or key.endswith("_uri")
        or key.endswith("_at") or key.endswith("_date") or key in ("date", "due_date")
        or "email" in key or "avatar" in key
        or key in _RESERVED
    )


def _name_of(obj) -> str | None:
    if isinstance(obj, dict):
        for k in _NAME_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        # A Linear cycle is usually unnamed; its number is how people say it.
        number = obj.get("number")
        if isinstance(number, (int, float)) and not isinstance(number, bool):
            return f"#{number:g}"
    return None


def _text(value: str) -> str | None:
    value = value.strip()
    if not value or len(value) > MAX_TEXT or _EMAIL.search(value):
        return None
    if value.startswith(("http://", "https://")):
        return None
    return value


def _simple(value):
    """The storable form of one value, or None to drop it."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        # A GraphQL connection: {"nodes": [...]} is a list of names.
        if isinstance(value.get("nodes"), list):
            return _simple(value["nodes"])
        name = _name_of(value)
        return _text(name) if name else None
    if isinstance(value, list):
        names = []
        for item in value[:MAX_LIST]:
            if isinstance(item, str):
                item = _text(item)
            elif isinstance(item, dict):
                item = _name_of(item)
                item = _text(item) if item else None
            else:
                item = None
            if item and item not in names:
                names.append(item)
        return names or None
    return None


def simple_fields(payload: dict, *, skip: tuple[str, ...] = ()) -> dict:
    """Every simple field of one API object, normalized. Never raises.

    ``skip`` names keys the caller already stores in a real column (the
    author, the repository, the state), so they are not stored twice.
    """
    out: dict = {}
    if not isinstance(payload, dict):
        return out
    skipped = {normalize_key(k) for k in skip}
    for raw_key, raw_value in payload.items():
        if len(out) >= MAX_FIELDS:
            break
        key = normalize_key(raw_key)
        if not key or key in skipped or _dropped_key(key):
            continue
        value = _simple(raw_value)
        if value is None or value == [] or value == "":
            continue
        out[key] = value
    return out
