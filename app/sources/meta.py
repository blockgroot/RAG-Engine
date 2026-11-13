"""People, links and containers captured at sync, for the knowledge graph.

The graph is rebuilt from the DATABASE, never by re-calling a provider, so what
is not captured here does not exist for it. Three lists:

* ``people``: ``{key, provider, external_id, email, name, role}``. ``key`` is
  ``<provider>:<external_id>``, else ``email:<address>``; NEVER a display name
  (merging people by name is the one mistake the graph must not make).
* ``links``: ``{provider, external_id, url}``, only URLs that resolve to a
  provider id.
* ``containers``: ``{provider, kind, external_id, name}``.

Every list is CAPPED and a cut sets ``truncated``. Filled only from payloads
already fetched; never an API call.
"""

from __future__ import annotations

import re

MAX_PEOPLE = 50
MAX_LINKS = 50
MAX_CONTAINERS = 20

#: Which person is "the editor": the one ``source_last_editor`` already shows.
_EDITOR_ROLES = ("editor", "author", "assignee")


def person_key(provider: str, external_id: str | None, email: str | None) -> str | None:
    """The identity key: a provider id when stable, else the email, else nothing."""
    external_id = (external_id or "").strip()
    if external_id:
        return f"{provider}:{external_id}"
    email = (email or "").strip().lower()
    if email:
        return f"email:{email}"
    return None


def person(
    provider: str,
    *,
    role: str,
    external_id: str | None = None,
    email: str | None = None,
    name: str | None = None,
) -> dict | None:
    """One person entry, or ``None`` with no id or email (never keyed by name)."""
    key = person_key(provider, external_id, email)
    if key is None:
        return None
    entry = {"key": key, "provider": provider, "role": role}
    if external_id:
        entry["external_id"] = external_id.strip()
    if email and email.strip():
        entry["email"] = email.strip().lower()
    if name and name.strip():
        entry["name"] = name.strip()
    return entry


def container(provider: str, kind: str, external_id: str | None, name: str | None = None) -> dict | None:
    if not external_id:
        return None
    entry = {"provider": provider, "kind": kind, "external_id": external_id}
    if name and name.strip():
        entry["name"] = name.strip()
    return entry


def link(provider: str, external_id: str, url: str | None = None) -> dict:
    entry = {"provider": provider, "external_id": external_id}
    if url:
        entry["url"] = url
    return entry


def _dedupe(items: list[dict | None], identity, cap: int) -> tuple[list[dict], bool]:
    seen: set = set()
    kept: list[dict] = []
    truncated = False
    for item in items:
        if not item:
            continue
        ident = identity(item)
        if ident in seen:
            continue
        seen.add(ident)
        if len(kept) >= cap:
            truncated = True
            break
        kept.append(item)
    return kept, truncated


def build_meta(
    *,
    people: list[dict | None] = (),
    links: list[dict | None] = (),
    containers: list[dict | None] = (),
) -> dict | None:
    """Assemble, deduplicate and cap. ``None`` when there is nothing at all."""
    kept_people, cut_people = _dedupe(list(people), lambda p: (p["key"], p["role"]), MAX_PEOPLE)
    kept_links, cut_links = _dedupe(
        list(links), lambda l: (l["provider"], l["external_id"]), MAX_LINKS
    )
    kept_containers, cut_containers = _dedupe(
        list(containers), lambda c: (c["provider"], c["kind"], c["external_id"]), MAX_CONTAINERS
    )
    if not (kept_people or kept_links or kept_containers):
        return None
    meta: dict = {}
    if kept_people:
        meta["people"] = kept_people
    if kept_links:
        meta["links"] = kept_links
    if kept_containers:
        meta["containers"] = kept_containers
    if cut_people or cut_links or cut_containers:
        meta["truncated"] = True
    return meta


def merge_meta(base: dict | None, *, links: list[dict] = ()) -> dict | None:
    """Add links found elsewhere (the document body) to an adapter's record."""
    base = base or {}
    return build_meta(
        people=list(base.get("people") or []),
        links=list(base.get("links") or []) + list(links),
        containers=list(base.get("containers") or []),
    ) if (base or links) else None


def editor_key(meta: dict | None) -> str | None:
    """The identity behind ``source_last_editor``, when the adapter captured one."""
    people = (meta or {}).get("people") or []
    for role in _EDITOR_ROLES:
        for entry in people:
            if entry.get("role") == role and entry.get("key"):
                return entry["key"]
    return None


# -- links found in a document's text ----------------------------------------
#
# By URL SHAPE only, resolved to the id that provider's adapter stores.

_URL = re.compile(r"https?://[^\s<>|)\]\"']+")
_LINEAR = re.compile(r"^https?://linear\.app/[^/]+/issue/([A-Za-z][A-Za-z0-9]*-\d+)")
_GITHUB = re.compile(r"^https?://github\.com/([^/\s]+)/([^/\s]+)/(pull|issues)/(\d+)")
_NOTION = re.compile(r"^https?://(?:www\.)?notion\.so/\S*?([0-9a-f]{32})(?:[?#]|$)", re.I)
_DRIVE = re.compile(
    r"^https?://(?:docs|drive)\.google\.com/(?:document|file|spreadsheets|presentation)/d/([A-Za-z0-9_-]{10,})"
)
_SLACK = re.compile(r"^https?://[^/]+\.slack\.com/archives/([A-Z0-9]+)/p(\d{10})(\d{6})(?:\?(\S*))?")
_SLACK_THREAD = re.compile(r"(?:^|&)thread_ts=(\d{10}\.\d{6})")


def _notion_uuid(hex32: str) -> str:
    h = hex32.lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def resolve_link(url: str) -> dict | None:
    """Map one URL to ``{provider, external_id, url}``, or ``None`` if unknown."""
    url = url.rstrip(".,;:")
    if m := _LINEAR.match(url):
        # The IDENTIFIER (all a URL carries), resolved against the stored title.
        return link("linear", m.group(1).upper(), url)
    if m := _GITHUB.match(url):
        owner, repo, kind, number = m.groups()
        prefix = "pr" if kind == "pull" else "issue"
        return link("github", f"{prefix}:{owner}/{repo}#{number}".lower(), url)
    if m := _NOTION.match(url):
        return link("notion", _notion_uuid(m.group(1)), url)
    if m := _DRIVE.match(url):
        return link("google", m.group(1), url)
    if m := _SLACK.match(url):
        channel, secs, micros, query = m.groups()
        thread = _SLACK_THREAD.search(query or "")
        # A reply's permalink points at its THREAD (`<channel>:<thread_ts>`).
        ts = thread.group(1) if thread else f"{secs}.{micros}"
        return link("slack", f"{channel}:{ts}", url)
    return None


def extract_links(text: str | None) -> list[dict]:
    """Every resolvable link in ``text``, in order of appearance, deduplicated."""
    if not text:
        return []
    found: list[dict] = []
    seen: set = set()
    for match in _URL.finditer(text):
        resolved = resolve_link(match.group(0))
        if resolved is None:
            continue
        ident = (resolved["provider"], resolved["external_id"])
        if ident in seen:
            continue
        seen.add(ident)
        found.append(resolved)
    return found
