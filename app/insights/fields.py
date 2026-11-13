"""Keep every SIMPLE field a source already returned, for charts.

What survives ``simple_fields`` is stored in ``activity_facts.attrs``, and
``attr_catalog`` discovers what exists, so a field is chartable the day a
tool sends it. Each rule is a size or privacy bound:

- short values only (``MAX_TEXT``, numbers, booleans, short name lists);
- no ids, links or timestamps (they group nothing);
- no email addresses anywhere (a person is ``actor``);
- a nested object becomes its name, never the object;
- at most ``MAX_FIELDS`` keys per item.

Keys are normalized at WRITE and checked again at READ (``KEY_RE``): that is
what lets a key be a JSON key literal in SQL.
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
    ``skip`` names keys the caller already stores in a real column."""
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
