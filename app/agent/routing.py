"""Pick the agent for a question, so the member does not have to.

The corpus is chosen by measurement, not an LLM or keywords: which
provider's content resembles the question (one embedding, one grouped
query). The chart check selects a registry metric, never a source by name.

GitHub embeds nothing, so it can never win a cosine race. It is reached by
a named authorized repo (strong) or code intent when nothing clears the
gate (weak, last).

Routing never decides whether an answer exists: the routed agent still runs
the gate and the strict prompt, so a misroute costs a refusal, never a
wrong answer.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from ..config.settings import EmbeddingSettings, RagSettings
from .orchestration import DIRECT_AGENT_KEYS, INSIGHTS_KEY, POLICY_KEY, WORKSPACE_KEY

_ROUTING_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="routing-probe")

logger = logging.getLogger(__name__)

# The probe is a hint on every question's critical path, so it gets a short
# ceiling: a hanging embedder must not add EMBEDDING_TIMEOUT before the
# pipeline even starts.
DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0

_probe_provider = None
_probe_lock = threading.Lock()


def _probe_embedder():
    """Cached embedder for the routing probe. Same MODEL, shorter timeout.

    Same model, or every cosine against stored chunks is meaningless. Cached
    because the remote provider builds a fresh HTTP client per construction.
    """
    global _probe_provider
    if _probe_provider is not None:
        return _probe_provider

    from ..embeddings import build_embedding_provider

    with _probe_lock:
        if _probe_provider is None:
            settings = EmbeddingSettings.from_env()
            if settings.backend != "local":
                ceiling = float(
                    os.getenv("AGENT_ROUTING_PROBE_TIMEOUT")
                    or DEFAULT_PROBE_TIMEOUT_SECONDS
                )
                settings = replace(
                    settings, timeout=min(settings.timeout, ceiling)
                )
            _probe_provider = build_embedding_provider(settings)
    return _probe_provider


def reset_probe_embedder_for_tests() -> None:
    """Drop the cached probe embedder (tests that change EMBEDDING_* env)."""
    global _probe_provider
    with _probe_lock:
        _probe_provider = None


#: Providers with embedded content to score against. GitHub is absent by design.
EMBEDDED_PROVIDERS = ("notion", "google", "slack", "linear")

#: Words that mean "the codebase" strongly enough to reach for GitHub when
#: nothing embedded answers. Narrow: a word two sources share is worse than none.
_CODE_INTENT = re.compile(
    r"\b("
    r"repo|repos|repositor(?:y|ies)"
    r"|commit|commits|committed"
    r"|pull request|pull requests|\bPRs?\b"
    r"|branch|branches|merged?"
    # Review words are safe because code intent is checked AFTER the probe: a
    # document question above the gate already won.
    r"|reviewer|reviewers|code review|approved this|who approved"
    r"|codebase|source code|github"
    r"|changelog|release notes"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RoutingDecision:
    """Which agent was chosen, and enough to explain why in a log or a test."""

    agent_key: str
    #: ``only-source``, ``repo-named``, ``best-match``, ``code-intent``,
    #: ``weak-best-match``, ``no-sources``, or ``requested``.
    reason: str
    #: provider -> best cosine. Empty when no probe ran. Logged to tune the gate.
    scores: dict[str, float] = field(default_factory=dict)
    chart_spec: object | None = None
    chart_refusal: str | None = None
    #: The check's ``needs_live`` verdict (None when it did not run or say);
    #: the chat edge hands it to the live-tools gateway.
    needs_live: bool | None = None
    #: The question check's ``chart_ask`` ("visual" / "count" / None), so the
    #: chat can offer Chart mode beside a written answer to a count.
    chart_ask: str | None = None
    #: Figures worked out over a sheet's rows (``insights.table_answers``), one
    #: passage per sheet. Empty for every other question.
    table_answers: tuple = ()


#: What a member calls each service. Tight: an alias that appears innocently
#: in a sentence would hijack routing. Shared by the scheduler's provider pick
#: and a DM's scope pick, which must read the same names.
PROVIDER_ALIASES: dict[str, tuple[str, ...]] = {
    "github": ("github",),
    "slack": ("slack",),
    "linear": ("linear",),
    "notion": ("notion",),
    "google": ("google drive", "google", "drive", "gdrive"),
}


def named_providers(text: str, available) -> set[str]:
    """EVERY service this text names (a connected answer reads them all; routing
    uses ``named_provider``, which picks none of two)."""
    lowered = (text or "").lower()
    return {
        provider
        for provider in available
        for alias in PROVIDER_ALIASES.get(provider, ())
        if re.search(rf"\b{re.escape(alias)}\b", lowered)
    }


def named_provider(text: str, available) -> str | None:
    """The service this text NAMES, when it names exactly one. Two named
    return None: the wrong one is worse than the measurement."""
    lowered = (text or "").lower()
    hits = {
        provider
        for provider in available
        for alias in PROVIDER_ALIASES.get(provider, ())
        if re.search(rf"\b{re.escape(alias)}\b", lowered)
    }
    return hits.pop() if len(hits) == 1 else None


def _connected_providers(org_id: str, workspace_id: str | None) -> set[str]:
    """Providers connected IN THIS SCOPE, never the org's: a space must not
    be routed to a source it cannot read."""
    from ..db.connection import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT provider FROM oauth_connections "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "AND needs_reauth = false",
            (org_id, workspace_id),
        ).fetchall()
    return {r[0] for r in rows}


def _repo_named_in(question: str, repos) -> str | None:
    """The authorized repo this question names, if any.

    Shared by agent selection and a DM's scope selection, so they agree.
    AUTHORIZED repos only, so a public repo name cannot steer it. Both
    ``owner/name`` and bare ``name`` count.
    """
    lowered = question.lower()
    for repo in repos or []:
        full = str((repo or {}).get("full_name") or "")
        if not full:
            continue
        short = full.split("/")[-1]
        # Bounded by word edges so "api" inside "rapidly" is not a repo hit.
        for candidate in (full, short):
            if candidate and re.search(
                rf"(?<![\w/-]){re.escape(candidate.lower())}(?![\w-])", lowered
            ):
                return full
    return None


def _named_repo(question: str, org_id: str, workspace_id: str | None) -> str | None:
    """A GitHub repo this question names by name, in THIS scope."""
    from ..auth.credentials import get_connection_config

    try:
        config = get_connection_config(org_id, "github", workspace_id) or {}
    except Exception:  # noqa: BLE001 - routing must never fail a question
        return None
    return _repo_named_in(question, config.get("repos"))


#: ``probe_best_scope`` found nothing indexed. Distinct because ``None`` is
#: org-wide.
_NO_MATCH = object()


def _probe_scores(
    question: str,
    org_id: str,
    workspace_id: str | None,
    candidates: set[str],
) -> dict[str, float]:
    """Best cosine per provider for this question, in one grouped query.

    ``org_id`` and ``workspace_id`` are in the WHERE: routing must never be
    informed by content the asker cannot read.
    """
    providers = sorted(candidates & set(EMBEDDED_PROVIDERS))
    if not providers:
        return {}

    from ..db.connection import get_connection

    try:
        vector = _probe_embedder().embed([question])[0]
    except Exception:  # noqa: BLE001 - fall through to the default agent
        logger.warning("Agent routing: could not embed the question", exc_info=True)
        return {}

    with get_connection(org_id=org_id) as conn:
        rows = conn.execute(
            """
            SELECT d.source_provider,
                   MAX(1 - (c.embedding <=> %s::halfvec)) AS best
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.org_id = %s
              AND c.workspace_id IS NOT DISTINCT FROM %s
              AND d.source_provider = ANY(%s)
            GROUP BY d.source_provider
            """,
            (vector, org_id, workspace_id, providers),
        ).fetchall()
    return {row[0]: float(row[1]) for row in rows if row[1] is not None}


def _words(text: str) -> set[str]:
    """Lowercased alphanumeric tokens, crudely singularised, so a title
    retyped from memory still matches."""
    return {
        w[:-1] if len(w) > 3 and w.endswith("s") else w
        for w in re.findall(r"[a-z0-9]+", text.lower())
    }



def _connection_scopes(org_id: str, scope_ids: list[str | None]):
    """``(workspace_id, provider, repos)`` for every live connection in these
    scopes. One query for both rungs (GitHub has no chunks to be seen by)."""
    from ..db.connection import get_connection

    spaces = [sid for sid in scope_ids if sid is not None]
    try:
        with get_connection() as conn:
            return conn.execute(
                "SELECT workspace_id::text, provider, source_config->'repos' "
                "FROM oauth_connections "
                "WHERE org_id = %s::uuid AND needs_reauth = false "
                "AND ((%s AND workspace_id IS NULL) "
                "     OR workspace_id = ANY(%s::uuid[]))",
                (org_id, None in scope_ids, spaces),
            ).fetchall()
    except Exception:  # noqa: BLE001 - routing must never fail a question
        logger.warning("Scope routing: could not read connections", exc_info=True)
        return []


def _code_scope(org_id: str, scopes: list[tuple[str | None, str]], question: str):
    """The scope holding GitHub, for a code question no corpus can answer.

    Below the probe (a scoring document question wins) and above the weak
    fall-through. Exactly one candidate or nothing.
    """
    if not _CODE_INTENT.search(question):
        return _NO_MATCH
    candidates = {
        scope_id
        for scope_id, provider, repos in _connection_scopes(
            org_id, [sid for sid, _ in scopes]
        )
        if provider == "github" and repos
    }
    if len(candidates) == 1:
        return candidates.pop()
    return _NO_MATCH


def _named_scope(org_id: str, scopes: list[tuple[str | None, str]], question: str):
    """The scope whose SPACE NAME, DOCUMENT TITLE or REPO the question names.

    Ahead of the probe: a document ABOUT meeting notes can out-score the notes.
    Every token of the name must appear; two matching scopes resolve to neither.
    """
    asked = _words(question)
    if not asked:
        return _NO_MATCH

    candidates: set[str | None] = set()
    for scope_id, name in scopes:
        tokens = _words(name)
        if tokens and tokens <= asked:
            candidates.add(scope_id)

    from ..db.connection import get_connection

    ids = [sid for sid, _ in scopes]
    spaces = [sid for sid in ids if sid is not None]
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT title, workspace_id::text FROM documents "
            "WHERE org_id = %s::uuid AND title IS NOT NULL "
            "AND ((%s AND workspace_id IS NULL) OR workspace_id = ANY(%s::uuid[]))",
            (org_id, None in ids, spaces),
        ).fetchall()
    conn_rows = _connection_scopes(org_id, ids)
    for title, scope_id in rows:
        tokens = _words(title or "")
        # A single generic token ("notes") is not a name; two is a title.
        if len(tokens) >= 2 and tokens <= asked:
            candidates.add(scope_id)

    # A repo name names this scope too: GitHub embeds nothing, so without this
    # it is unreachable from a DM.
    for scope_id, provider, repos in conn_rows:
        if provider == "github" and _repo_named_in(question, repos):
            candidates.add(scope_id)

    # Naming the connector is a name too, and the only signal a source with no
    # corpus has when no repo is named. It must sit above the probe, which
    # clears 0.35 on the company corpus for almost anything. One connector or none.
    spoken = named_provider(question, {provider for _, provider, _ in conn_rows})
    if spoken is not None:
        candidates |= {
            scope_id for scope_id, provider, _ in conn_rows if provider == spoken
        }

    if len(candidates) == 1:
        return candidates.pop()
    return _NO_MATCH



def choose_scope(
    org_id: str, scopes: list[tuple[str | None, str]], question: str
):
    """Which of the caller's scopes should answer (the Slack DM entry point).

    A DM has no space context, so it is inferred here; agent selection inside
    the chosen scope is then the same ``choose_agent`` the web chat runs.

    1. A NAME the asker typed (space, document title, authorized repo).
    2. The best-scoring scope, if it clears the 0.35 gate.
    3. Code intent when nothing cleared (a GitHub-only scope has no chunks).
    4. The best score anyway, then ``_NO_MATCH``; that scope's gate refuses.

    Returns the ``workspace_id`` (``None`` = org-wide) or ``_NO_MATCH``.
    """
    named = _named_scope(org_id, scopes, question)
    if named is not _NO_MATCH:
        return named

    best = _probe_scope_best(question, org_id, [sid for sid, _ in scopes])
    if best is not None and best[1] >= RagSettings.from_env().similarity_threshold:
        return best[0]

    code = _code_scope(org_id, scopes, question)
    if code is not _NO_MATCH:
        return code

    return best[0] if best is not None else _NO_MATCH


def probe_best_scope(
    question: str, org_id: str, workspace_ids: list[str | None]
) -> str | None | object:
    """The best-scoring scope, or ``_NO_MATCH``. Score-free wrapper."""
    best = _probe_scope_best(question, org_id, workspace_ids)
    return _NO_MATCH if best is None else best[0]


def _probe_scope_best(
    question: str, org_id: str, workspace_ids: list[str | None]
) -> tuple[str | None, float] | None:
    """``(workspace_id, best cosine)`` for the closest scope, in ONE query.

    The scopes are the caller's own (membership resolved before). Picks one
    scope, never blends: a space's rows never mix with org-wide rows. The
    score is returned so the caller can compare it against the gate.
    ``None`` when nothing is indexed.
    """
    if not workspace_ids:
        return None

    from ..db.connection import get_connection

    try:
        vector = _probe_embedder().embed([question])[0]
    except Exception:  # noqa: BLE001 - caller falls back to org-wide
        logger.warning("Slack DM routing: could not embed the question", exc_info=True)
        return None

    # `= ANY(array)` cannot match NULL, so org-wide gets its own IS NULL branch.
    spaces = [w for w in workspace_ids if w is not None]
    include_org_wide = None in workspace_ids

    with get_connection(org_id=org_id) as conn:
        rows = conn.execute(
            """
            SELECT c.workspace_id::text,
                   MAX(1 - (c.embedding <=> %s::halfvec)) AS best
            FROM chunks c
            WHERE c.org_id = %s
              AND ((%s AND c.workspace_id IS NULL)
                   OR c.workspace_id = ANY(%s::uuid[]))
            GROUP BY c.workspace_id
            ORDER BY best DESC
            LIMIT 1
            """,
            (vector, org_id, include_org_wide, spaces),
        ).fetchall()
    if not rows or rows[0][1] is None:
        return None
    return rows[0][0], float(rows[0][1])


def _has_authorized_repos(org_id: str, workspace_id: str | None) -> bool:
    """Whether GitHub has AUTHORIZED REPOS here. Not ``activity_facts``: it
    reads live, so a fresh installation can answer before any sync."""
    from ..auth.credentials import get_connection_config

    try:
        config = get_connection_config(org_id, "github", workspace_id) or {}
    except Exception:  # noqa: BLE001 - routing must never fail a question
        return False
    return bool(config.get("repos"))


def _chartable_tables(org_id: str, workspace_id: str | None, viewer, question: str = "",
                      uploads=None, *, providers: list[str] | None = None,
                      tag: str | None = None) -> list:
    """Tables THIS person may chart, for the chart classifier.

    This chat's uploads first, then document tables ranked by document
    similarity (``providers``/``tag`` narrow to a connector/channel). No
    viewer means none: tables are offered only through the access predicate.
    Never raises.
    """
    if viewer is None or getattr(viewer, "is_unrestricted", False):
        return []
    return _upload_tables(org_id, uploads) + _document_tables(
        org_id, workspace_id, viewer, question, providers=providers, tag=tag)


def _upload_tables(org_id: str, uploads) -> list:
    """Tables in this chat's uploads; a table-less file is read for figures
    once, on demand. Never raises."""
    if uploads is None:
        return []
    from ..doctables.store import list_upload_tables

    try:
        from ..doctables.factory import build_upload_text_adapter
        from ..doctables.uploads import read_upload_figures
        from ..llm.factory import build_llm_provider

        read_upload_figures(org_id=org_id, uploads=uploads,
                            adapter=build_upload_text_adapter(build_llm_provider()))
    except Exception:  # noqa: BLE001 - costs the figures, never the real tables
        logger.warning("Agent routing: could not read figures from uploads", exc_info=True)
    try:
        return list_upload_tables(org_id=org_id, uploads=uploads)
    except Exception:  # noqa: BLE001
        logger.warning("Agent routing: could not list uploaded tables", exc_info=True)
        return []


def _document_tables(org_id: str, workspace_id: str | None, viewer, question: str, *,
                     providers: list[str] | None = None, tag: str | None = None,
                     limit: int | None = None) -> list:
    try:
        from ..doctables.store import list_tables, typed_cells
        from ..insights import tables as doc_tables

        listed = doc_tables.distinct(list_tables(org_id=org_id, workspace_id=workspace_id,
                                                 viewer=viewer, providers=providers, tag=tag))
        from ..insights.resolve import title_match

        # The page they named is the LONGEST title run in the question, so a shared
        # word does not name a second page.
        matched = {id(t): title_match(t.document_title or "", question) for t in listed}
        best = max(matched.values(), default=0)
        named = sorted((t for t in listed if best >= 8 and matched[id(t)] == best),
                       key=lambda t: -doc_tables.label_hits(question, t))
        rest = [t for t in listed if t not in named]
        signals = doc_tables.document_similarity(question, rest, org_id=org_id,
                                                 workspace_id=workspace_id)
        if signals is None:
            ranked = doc_tables.rank(
                rest, None, floor=RagSettings.from_env().similarity_threshold,
                question=question, limit=limit or doc_tables.MAX_OFFERED,
            )
        else:
            cosine, keywords = signals
            ranked = doc_tables.rank(
                rest, cosine, keywords=keywords,
                floor=RagSettings.from_env().similarity_threshold, question=question,
                limit=limit or doc_tables.MAX_OFFERED,
            )
        # The named page is always offered, or closer tables fill the cap.
        offered = named + ranked
        # A table holding a cell the question TYPES comes first and says so.
        typed = typed_cells(question, listed)
        if typed:
            from dataclasses import replace

            first = [replace(t, typed=typed[t.id]) for t in offered if t.id in typed]
            first += [replace(t, typed=typed[t.id]) for t in listed
                      if t.id in typed and t not in offered]
            offered = first + [t for t in offered if t.id not in typed]
        return offered[: limit or doc_tables.MAX_OFFERED]
    except Exception:  # noqa: BLE001
        logger.warning("Agent routing: could not list document tables", exc_info=True)
        return []


def _chart_fields(org_id: str, workspace_id: str | None, viewer) -> dict | None:
    """Recorded fields that exist in this scope, per metric (`attr_catalog`).
    ``None`` (no viewer or failure) falls back to display hints. Never raises."""
    if viewer is None:
        return None
    try:
        from ..insights import attr_catalog

        return attr_catalog.by_metric(attr_catalog.discover_scope(
            org_id=org_id, workspace_id=workspace_id, viewer=viewer,
        ))
    except Exception:  # noqa: BLE001
        logger.warning("Agent routing: could not read recorded fields", exc_info=True)
        return None


def _chart_names(org_id: str, workspace_id: str | None, viewer) -> dict | None:
    """Real teams, repos, channels and people with activity here, so a focus is
    one that exists. ``None`` without a viewer or on failure. Never raises."""
    if viewer is None:
        return None
    try:
        from ..insights import scopes, store as fact_store

        return fact_store.scope_names(
            org_id=org_id, workspace_id=workspace_id,
            days=max(scopes.WINDOW_DAYS.values()), viewer=viewer,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Agent routing: could not read names for charts", exc_info=True)
        return None


def _chart_unsupported_model() -> str | None:
    """The message for Chart mode on a model that cannot chart, else None.
    Covers a stale selection or a direct API call (the picker hides them)."""
    from ..config.settings import LLMSettings
    from ..insights.resolve import unsupported_model_message
    from ..llm import catalog
    from ..llm.routed import selected_model

    model = selected_model()
    if catalog.supports_charts(model):
        return None
    choice = catalog.get(model)
    label = choice.label if choice else model
    alternatives = [LLMSettings.from_env().model or "the default model"] + [
        m.label for m in catalog.chart_models()
    ]
    return unsupported_model_message(label, alternatives)


def _table_answers_on() -> bool:
    try:
        from ..config.settings import DocTablesSettings

        return DocTablesSettings.from_env().answers_enabled
    except Exception:  # noqa: BLE001 - off is the safe reading
        return False


def _try_question_route(
    question: str,
    connected: set[str],
    org_id: str,
    workspace_id: str | None,
    live_out: list | None = None,
    chart_out: list | None = None,
    chart_from_words: bool = False,
    viewer=None,
    context: str | None = None,
) -> RoutingDecision | None:
    """Ask's small check: a live code question, and the live-data verdict.

    No metrics or tables (charts have their own mode). None for ordinary Q&A,
    so the cosine router runs. Never raises. ``live_out`` gets ``needs_live``.
    """
    from ..insights.resolve import CHART_MODE_HINT, classify_route, with_earlier

    tables_on = viewer is not None and _table_answers_on()
    # A follow-up is read with the question it continues (table check only).
    checked = with_earlier(question, context) if tables_on else question
    # Beside the check, not after it: SQL only, so it adds no wait.
    names_sheet = (_ROUTING_POOL.submit(_names_a_sheet, checked, org_id, workspace_id, viewer)
                   if tables_on else None)
    try:
        intent = classify_route(checked, github="github" in connected, table=tables_on)
    except Exception:  # noqa: BLE001
        logger.warning("Agent routing: question check failed", exc_info=True)
        return None
    if live_out is not None:
        live_out.append(getattr(intent, "needs_live", None))
    if chart_out is not None:
        chart_out.append(getattr(intent, "chart_ask", None))
    ask = getattr(intent, "chart_ask", None)
    if chart_from_words and ask in ("visual", "count"):
        # Slack has no toggle: a chart or count ask IS Chart mode there.
        return _chart_mode_route(question, connected, org_id, workspace_id, viewer)
    if ask == "visual":
        # A chart ask in web chat is pointed at Chart mode, not answered in words.
        return RoutingDecision(INSIGHTS_KEY, "chart-mode-off", chart_refusal=CHART_MODE_HINT)
    table_ask = getattr(intent, "table_ask", False)
    if tables_on and not table_ask and names_sheet is not None:
        # The model's yes/no is unstable, so a question typing a sheet's cell or
        # naming its column is asked of the form too.
        try:
            table_ask = names_sheet.result(timeout=_NAMES_SHEET_WAIT)
        except Exception:  # noqa: BLE001 - a hint may only ever add
            logger.warning("Agent routing: sheet check failed", exc_info=True)
    if tables_on and table_ask:
        # A figure over a sheet's rows, worked out by a checked query and given as
        # a passage; nothing fits, Ask goes on as before.
        from ..insights.table_answers import ANSWER_OFFERED, answer

        worked = answer(checked, _document_tables(org_id, workspace_id, viewer, checked,
                                                  limit=ANSWER_OFFERED))
        if worked:
            return RoutingDecision(worked[0].provider, "table-answer",
                                   table_answers=tuple(worked))
    if ask == "count":
        # A count over rows a sheet never embeds. Counted by SQL only when the chart
        # is NARROWED by a filter or focus from the question: an unnarrowed one
        # answers a broader question and reads as the answer.
        counted = _chart_mode_route(question, connected, org_id, workspace_id, viewer)
        spec = getattr(counted, "chart_spec", None)
        if spec is not None and (getattr(spec, "filters", None) or getattr(spec, "focus", None)):
            return replace(counted, reason="count-chart")
    if intent.kind != "github_live":
        return None
    # GitHub can never win the probe and no regex separates "auth code" from
    # "code of conduct". This check fails open, so `_CODE_INTENT` stays as the floor.
    if not _has_authorized_repos(org_id, workspace_id):
        logger.info(
            "Agent routing: classified code question but no authorized "
            "repos for org %s", org_id,
        )
        return None
    logger.info("Agent routing: %r classified as a live GitHub question", question[:60])
    return RoutingDecision("github", "classified-code-question")


def _document_gap(question, intent, tables, org_id, workspace_id, viewer, user_id):
    """A named page this space cannot chart, or None. Never raises."""
    from ..insights.resolve import chart_document_gap

    try:
        from ..doctables.store import document_places

        places = document_places(
            org_id=org_id, workspace_id=workspace_id, viewer=viewer, user_id=user_id,
            title_query=getattr(intent, "named_document", None),
        )
    except Exception:  # noqa: BLE001
        logger.warning("Agent routing: could not look up named documents", exc_info=True)
        places = []
    return chart_document_gap(
        question, getattr(intent, "named_document", None), tables, places,
    )


def _live_fetch_named_tables(org_id, workspace_id, viewer, title,
                             providers: list[str] | None = None) -> str | None:
    """Read one named, already-indexed Notion, Drive or Linear document live
    and save its figures (only when Chart mode has no table for it).

    Returns ``"saved"``, the title when it held no series, or None. Never raises.
    """
    try:
        from ..auth.credentials import get_live_connection_token
        from ..doctables.base import DocumentText
        from ..doctables.store import find_chart_document, replace_document_tables
        from ..doctables.table_adapter import TableAdapter
        from ..livetools.base import OK
        from ..livetools.drive import read_file
        from ..livetools.linear import read_issue
        from ..livetools.notion import read_page

        found = find_chart_document(
            org_id=org_id, workspace_id=workspace_id, viewer=viewer, title_query=title,
            providers=providers,
        )
        if found is None:
            return None
        document_id, provider, external_id, doc_title = found
        reader = {"notion": read_page, "google": read_file, "linear": read_issue}.get(provider)
        if reader is None:
            return None
        token = get_live_connection_token(org_id, provider, workspace_id=workspace_id)
        read = reader(token, external_id)
        if read.outcome != OK or not read.text:
            return None
        doc = DocumentText(
            external_id=external_id, title=doc_title or title, content=read.text,
        )
        adapter = TableAdapter()
        tables = adapter.extract(doc)
        origin = adapter.origin
        if not tables:
            from ..doctables.factory import build_upload_text_adapter
            from ..doctables.queue import mark_read
            from ..llm.factory import build_llm_provider

            text = build_upload_text_adapter(build_llm_provider())
            if not text.wants(doc):
                return doc_title or title
            tables = text.extract(doc)
            origin = text.origin
            if not tables:
                mark_read(
                    document_id, org_id=org_id, workspace_id=workspace_id,
                    external_id=external_id, title=doc_title or title,
                )
                return doc_title or title
        replace_document_tables(
            document_id, org_id=org_id, workspace_id=workspace_id, tables=tables,
            origin=origin,
        )
        return "saved"
    except Exception:  # noqa: BLE001 - a live read may only add a table
        logger.warning("Agent routing: live table read failed", exc_info=True)
        return None


def _worth_prose(intent, viewer) -> bool:
    """Chart mode has no chart yet and a real person is asking."""
    from ..vectorstore.base import Viewer

    if not isinstance(viewer, Viewer) or viewer.is_unrestricted:
        return False
    if getattr(intent, "kind", None) == "chart" and getattr(intent, "spec", None) is not None:
        return False
    return intent.kind in ("refuse", "qa", "chart")


def _chart_from_prose(question, intent, org_id, workspace_id, viewer, providers,
                      *, read_providers=None, tag=None):
    """Chart figures from the page the question matches, or say it had none.

    ``read_providers``/``tag`` keep the read on the named connector/channel.
    None keeps the caller's refusal. No activity metric is substituted.
    """
    if not _worth_prose(intent, viewer):
        return None
    from ..doctables.store import list_tables
    from ..doctables.text_adapter import read_for_chart
    from ..insights.resolve import (
        DEFAULT_PERIOD,
        TABLE_METRIC,
        ChartSpec,
        prose_figures_missing,
        prose_figures_unclear,
    )
    from ..insights import tables as doc_tables
    from ..llm.factory import build_llm_provider

    try:
        read = read_for_chart(
            org_id=org_id, workspace_id=workspace_id, viewer=viewer, question=question,
            providers=read_providers, llm=build_llm_provider(), tag=tag,
        )
    except Exception:  # noqa: BLE001
        logger.warning("Agent routing: paragraph read failed", exc_info=True)
        return None
    fresh = []
    if read.document_ids:
        try:
            listed = list_tables(org_id=org_id, workspace_id=workspace_id, viewer=viewer,
                                 providers=read_providers, tag=tag)
            wanted = set(read.document_ids)
            fresh = [table for table in listed if table.document_id in wanted]
        except Exception:  # noqa: BLE001
            logger.warning("Agent routing: could not list figures just read", exc_info=True)
            fresh = []
    if fresh:
        spec = _classify_tables(question, providers, fresh, org_id, workspace_id, viewer)
        if spec is not None:
            return RoutingDecision(INSIGHTS_KEY, "chart", chart_spec=spec)
        if len(fresh) == 1:
            pick = doc_tables.prose_chart(fresh[0])
            if pick is not None:
                return RoutingDecision(INSIGHTS_KEY, "chart", chart_spec=ChartSpec(
                    metric=TABLE_METRIC, group_by=pick.group_by, split_by=pick.split_by,
                    period=DEFAULT_PERIOD, chart=pick.chart, measure=pick.measure,
                    filters=pick.filters, table_id=pick.table_id, value=pick.value,
                ))
        return RoutingDecision(
            INSIGHTS_KEY, "chart-refuse",
            chart_refusal=prose_figures_unclear(
                sorted({table.document_title for table in fresh}),
                [table.name for table in fresh],
            ),
        )
    if read.empty_titles:
        return RoutingDecision(
            INSIGHTS_KEY, "chart-refuse",
            chart_refusal=prose_figures_missing(list(read.empty_titles)),
        )
    return None


def _classify_tables(question, providers, tables, org_id, workspace_id, viewer):
    """A chart spec on one of ``tables`` (and only those), or None. Never raises."""
    from ..insights.resolve import classify_question

    try:
        again = classify_question(
            question, providers=providers, fail_open=False, offer_github=False,
            tables=tables,
            fields=_chart_fields(org_id, workspace_id, viewer),
            names=_chart_names(org_id, workspace_id, viewer),
        )
    except Exception:  # noqa: BLE001
        logger.warning("Agent routing: chart of the read tables failed", exc_info=True)
        return None
    spec = getattr(again, "spec", None)
    if getattr(again, "kind", None) == "chart" and spec is not None and \
            spec.table_id in {table.id for table in tables}:
        return spec
    return None


def _channel_subject(org_id: str, workspace_id: str | None, tag: str) -> str | None:
    """The channel name its threads are counted under (``#general``), or None.
    Never raises."""
    try:
        from ..db.connection import get_connection

        with get_connection() as conn:
            row = conn.execute(
                "SELECT split_part(title, ':', 1) FROM documents "
                "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s::uuid "
                "AND source_provider = 'slack' AND tags @> ARRAY[%s::text] "
                "ORDER BY source_last_modified DESC NULLS LAST LIMIT 1",
                (org_id, workspace_id, tag),
            ).fetchone()
    except Exception:  # noqa: BLE001
        logger.warning("Agent routing: could not read the channel name", exc_info=True)
        return None
    if row is None:
        return None
    return (row[0] or "").strip() or None


#: Chart-mode replies that are not a missing chart: the question wanted words,
#: the model cannot chart, or Chart mode was off. Never logged as chart gaps.
NOT_CHART_GAPS = frozenset({
    "chart-mode-text-question", "chart-model-unsupported", "chart-mode-off",
})


#: Chart mode in a Slack channel, when the question is not about the channel.
CHANNEL_CHART_ONLY = (
    "**In a channel I chart this channel only**\n"
    "Ask about this channel's conversations or the figures posted in it. "
    "For company-wide charts, message me directly."
)


def _in_channel(spec, subject: str | None):
    """A spec reading only this channel, or None."""
    from ..insights import registry

    if spec.table_id:
        return spec
    metric = registry.METRICS.get(spec.metric)
    if metric is None or metric.provider != "slack" or not subject:
        return None
    return replace(spec, focus=subject)


def _chart_mode_route(
    question: str,
    connected: set[str],
    org_id: str,
    workspace_id: str | None,
    viewer=None,
    uploads=None,
    user_id: str | None = None,
    channel_tag: str | None = None,
) -> RoutingDecision:
    """Chart mode: a chart or a plain refusal, never a document answer or a
    guess. Never raises.

    Order: stored tables and activity; the named connector's tables alone;
    the named page read live; matching pages read for figures; then a refusal
    naming what failed. ``channel_tag`` charts that Slack channel only.
    """
    from ..insights import panels as panel_defs
    from ..insights.resolve import (
        CHART_MODE_TEXT_QUESTION,
        CannotChart,
        chart_mode_refusal,
        classify_question,
    )

    unsupported = _chart_unsupported_model()
    if unsupported:
        return RoutingDecision(INSIGHTS_KEY, "chart-model-unsupported",
                               chart_refusal=unsupported)
    providers = [p for p in sorted(connected) if panel_defs.for_provider(p)]
    subject = None
    if channel_tag:
        providers = [p for p in providers if p == "slack"]
        subject = _channel_subject(org_id, workspace_id, channel_tag)

    def chart(spec, reason="chart"):
        if channel_tag:
            spec = _in_channel(spec, subject)
            if spec is None:
                return RoutingDecision(INSIGHTS_KEY, "chart-refuse",
                                       chart_refusal=CHANNEL_CHART_ONLY)
        return RoutingDecision(INSIGHTS_KEY, reason, chart_spec=spec)

    tables = _chartable_tables(org_id, workspace_id, viewer, question, uploads, tag=channel_tag)
    try:
        intent = classify_question(
            question, providers=providers, fail_open=False, offer_github=False,
            tables=tables,
            fields=_chart_fields(org_id, workspace_id, viewer),
            names=_chart_names(org_id, workspace_id, viewer),
        )
    except CannotChart as exc:
        return RoutingDecision(INSIGHTS_KEY, "chart-refuse", chart_refusal=str(exc))
    except Exception:  # noqa: BLE001
        logger.warning("Agent routing: chart classifier failed", exc_info=True)
        return RoutingDecision(
            INSIGHTS_KEY, "chart-refuse",
            chart_refusal="**Couldn't build this chart just now**\nTry again in a moment.",
        )
    named = getattr(intent, "named_document", None)
    if intent.kind == "chart" and intent.spec is not None:
        # A page they NAMED with figures beats an activity metric.
        if intent.spec.table_id or not named or not _named_has_tables(
                org_id, workspace_id, viewer, named, channel_tag):
            return chart(intent.spec)
    source = getattr(intent, "source", None)
    only = [source] if source else None
    # They named a connector but the pick was another's: retry with its tables alone.
    if source and any(t.provider != source for t in tables):
        own = _document_tables(org_id, workspace_id, viewer, question,
                               providers=only, tag=channel_tag)
        spec = _classify_tables(question, providers, own, org_id, workspace_id,
                                viewer) if own else None
        if spec is not None:
            return chart(spec)
    # The named page may sit outside the offered tables: read it live once,
    # save it, and retry with only its tables.
    if named and not channel_tag and viewer is not None \
            and not getattr(viewer, "is_unrestricted", False):
        try:
            from ..doctables.store import list_tables

            extra = list_tables(org_id=org_id, workspace_id=workspace_id, viewer=viewer,
                                title_query=named, providers=only)
        except Exception:  # noqa: BLE001
            extra = []
        read = None
        if not extra:
            read = _live_fetch_named_tables(org_id, workspace_id, viewer, named, only)
            if read == "saved":
                try:
                    extra = list_tables(org_id=org_id, workspace_id=workspace_id,
                                        viewer=viewer, title_query=named, providers=only)
                except Exception:  # noqa: BLE001
                    extra = []
        if extra:
            spec = _classify_tables(question, providers, extra, org_id, workspace_id, viewer)
            if spec is not None:
                return chart(spec)
            if len(extra) == 1:
                prose = _single_table_chart(extra[0])
                if prose is not None:
                    return chart(prose)
            # They named this page: never answer from another that merely matches words.
            from ..insights.resolve import prose_figures_unclear

            return RoutingDecision(INSIGHTS_KEY, "chart-refuse", chart_refusal=prose_figures_unclear(
                sorted({table.document_title for table in extra}),
                [table.name for table in extra],
            ))
        elif read and read != "saved":
            from ..insights.resolve import prose_figures_missing

            return RoutingDecision(INSIGHTS_KEY, "chart-refuse",
                                   chart_refusal=prose_figures_missing([read]))
        else:
            # The named page is not here (another space, or not shared): say where it
            # is, and never read other pages that merely share words.
            gap = _document_gap(question, intent, tables, org_id, workspace_id, viewer, user_id)
            if gap:
                return RoutingDecision(INSIGHTS_KEY, "chart-refuse", chart_refusal=gap)
    prose = _chart_from_prose(
        question, intent, org_id, workspace_id, viewer, providers,
        read_providers=only or (["slack"] if channel_tag else None), tag=channel_tag,
    )
    if prose is not None:
        if prose.chart_spec is not None:
            return chart(prose.chart_spec)
        return prose
    if not channel_tag:
        gap = _document_gap(question, intent, tables, org_id, workspace_id, viewer, user_id)
        if gap:
            return RoutingDecision(INSIGHTS_KEY, "chart-refuse", chart_refusal=gap)
    if intent.kind == "refuse" and intent.message:
        return RoutingDecision(INSIGHTS_KEY, "chart-refuse", chart_refusal=intent.message)
    if intent.kind in ("qa", "github_live"):
        # They asked for words in Chart mode: say so, and how to get them.
        return RoutingDecision(INSIGHTS_KEY, "chart-mode-text-question",
                               chart_refusal=CHART_MODE_TEXT_QUESTION)
    return RoutingDecision(INSIGHTS_KEY, "chart-refuse",
                           chart_refusal=chart_mode_refusal(providers))


def _named_has_tables(org_id, workspace_id, viewer, named, channel_tag) -> bool:
    """The page the question named has figures this viewer may chart."""
    if channel_tag or viewer is None or getattr(viewer, "is_unrestricted", False):
        return False
    try:
        from ..doctables.store import list_tables

        return bool(list_tables(org_id=org_id, workspace_id=workspace_id, viewer=viewer,
                                title_query=named))
    except Exception:  # noqa: BLE001
        return False


def _single_table_chart(table):
    """The one obvious chart of a page's only table (``tables.prose_chart``)."""
    from ..insights import tables as doc_tables
    from ..insights.resolve import DEFAULT_PERIOD, TABLE_METRIC, ChartSpec

    pick = doc_tables.prose_chart(table)
    if pick is None:
        return None
    return ChartSpec(
        metric=TABLE_METRIC, group_by=pick.group_by, split_by=pick.split_by,
        period=DEFAULT_PERIOD, chart=pick.chart, measure=pick.measure,
        filters=pick.filters, table_id=pick.table_id, value=pick.value,
    )


# ponytail: calibrated on four live pairs (follow-ups 0.01-0.03 apart, new
# topics 0.24-0.28); log `scores` and re-fit if follow-ups start misrouting.
_FOLLOW_UP_MARGIN = 0.1


def refusal_retry(decision: RoutingDecision) -> tuple[str, set[str]] | None:
    """Where a REFUSED answer is asked once more: ``(agent, also_searched)``.

    The refusing tool is left out; the best other tool that cleared the gate
    answers, the rest searched beside it. Only for a probe's pick or a named
    sheet row: a named, graph-placed or requested tool stays the only one.
    """
    if decision.reason not in ("best-match", "follow-up", "rows-named") or not decision.scores:
        return None
    gate = RagSettings.from_env().similarity_threshold
    others = sorted((tool for tool, score in decision.scores.items()
                     if score >= gate and tool != decision.agent_key),
                    key=lambda tool: -decision.scores[tool])
    return (others[0], set(others[1:])) if others else None


def _no_clear_winner(scores: dict[str, float]) -> bool:
    """True when the best source leads the runner-up by less than the margin."""
    top = sorted(scores.values(), reverse=True)
    return not top or (len(top) > 1 and top[0] - top[1] < _FOLLOW_UP_MARGIN)


#: Seconds the sheet check may still take once the model's check is back.
_NAMES_SHEET_WAIT = 2.0


def _names_a_sheet(question: str, org_id: str, workspace_id: str | None, viewer) -> bool:
    """Does the question type a sheet cell this viewer can read, or name a
    column header by every word? The data's own words, no list."""
    from ..doctables.store import list_tables, typed_cells
    from ..insights.tables import _words

    tables = [t for t in list_tables(org_id=org_id, workspace_id=workspace_id, viewer=viewer)
              if t.origin != "text"]
    asked = _words(question)
    if any((words := _words(str(c.get("name", "")))) and words <= asked
           for t in tables for c in t.columns):
        return True
    return bool(typed_cells(question, tables))


def _rows_named_provider(question: str, org_id: str, workspace_id: str | None,
                         viewer, embedded: set[str]) -> str | None:
    """The one tool whose sheet rows the question names by a cell, or None
    (``doctables.store.rows_naming``, as this viewer). Never raises."""
    try:
        from ..doctables.store import rows_naming

        hits = rows_naming(question, org_id=org_id, workspace_id=workspace_id,
                           viewer=viewer, providers=sorted(embedded))
    except Exception:  # noqa: BLE001 - routing must never fail a question
        logger.warning("Agent routing: row lookup failed", exc_info=True)
        return None
    tools = {hit.provider for hit in hits}
    return next(iter(tools)) if len(tools) == 1 else None


def _graph_named_provider(graph_plan, embedded: set[str]) -> str | None:
    """The tool holding what the question names EXACTLY, per the graph.

    Exact identifiers only (``SYV-6``); fuzzy titles are the probe's job. Two
    tools resolve to neither. Waits at most ``_GRAPH_WAIT_SECONDS``.
    """
    if graph_plan is None:
        return None
    plan = graph_plan
    if hasattr(graph_plan, "result"):
        try:
            plan = graph_plan.result(timeout=_GRAPH_WAIT_SECONDS)
        except Exception:  # noqa: BLE001 - a slow or failed graph costs only this hint
            return None
    if plan is None:
        return None
    from ..graph.plan import provider_of

    tools = {provider_of(getattr(s, "key", "")) for s in plan.exact_seeds}
    tools.discard(None)
    if len(tools) != 1:
        return None
    tool = next(iter(tools))
    return tool if tool in embedded else None


#: How long routing waits for a graph plan started alongside it.
_GRAPH_WAIT_SECONDS = 1.5


def choose_agent(
    question: str,
    org_id: str,
    *,
    workspace_id: str | None = None,
    requested_agent: str | None = None,
    context: str | None = None,
    graph_plan=None,
    viewer=None,
    chart_mode: bool = False,
    chart_from_words: bool = False,
    uploads=None,
    user_id: str | None = None,
    channel_tag: str | None = None,
) -> RoutingDecision:
    """Which agent answers, plus the question check's live-data verdict.

    ``channel_tag`` charts one Slack channel. ``chart_from_words``: Slack's
    chart asks go to Chart mode. ``chart_mode``: a chart or a refusal only.
    ``viewer`` gates which document tables are offered; ``uploads`` offers
    this chat's uploaded tables in Chart mode."""
    live: list = []
    chart: list = []
    decision = _choose_agent(
        question, org_id, workspace_id=workspace_id, requested_agent=requested_agent,
        context=context, graph_plan=graph_plan, live_out=live, viewer=viewer,
        chart_mode=chart_mode, chart_out=chart, chart_from_words=chart_from_words,
        uploads=uploads, user_id=user_id, channel_tag=channel_tag,
    )
    return replace(decision, needs_live=live[0] if live else None,
                   chart_ask=chart[0] if chart else None)


def _choose_agent(
    question: str,
    org_id: str,
    *,
    workspace_id: str | None = None,
    requested_agent: str | None = None,
    context: str | None = None,
    graph_plan=None,
    live_out: list | None = None,
    viewer=None,
    chart_mode: bool = False,
    chart_out: list | None = None,
    chart_from_words: bool = False,
    uploads=None,
    user_id: str | None = None,
    channel_tag: str | None = None,
) -> RoutingDecision:
    """Decide which agent answers ``question``. Never raises.

    ``context`` is the previous question: when no source clearly wins on the
    question alone, it is re-probed with the turn it follows (a new topic has
    a clear winner and routes on its own words).

    1. An explicit ``agent`` request.
    2. Chart mode: a chart or a refusal.
    3. A classified live-code question (GitHub; the probe cannot reach it).
    4. A named repository (beats a page ABOUT the repo).
    4b. An exact identifier the graph places in one tool.
    5. One embedded source: no probe.
    6. The best-scoring provider, if it clears the gate.
    7. Code intent (keyword floor) when nothing cleared; kept because 3 fails open.
    8. The best score anyway; that agent's gate refuses honestly.
    9. The default agent when there is nothing to route to.
    """
    if requested_agent in DIRECT_AGENT_KEYS or requested_agent == INSIGHTS_KEY:
        return RoutingDecision(requested_agent, "requested")

    default_key = WORKSPACE_KEY if workspace_id is not None else POLICY_KEY

    try:
        connected = _connected_providers(org_id, workspace_id)
    except Exception:  # noqa: BLE001 - routing must never fail a question
        logger.warning("Agent routing: could not list connections", exc_info=True)
        if chart_mode:
            connected = set()
        else:
            return RoutingDecision(default_key, "no-sources")

    if chart_mode:
        return _chart_mode_route(question, connected, org_id, workspace_id, viewer,
                                 uploads=uploads, user_id=user_id, channel_tag=channel_tag)

    if not connected:
        return RoutingDecision(default_key, "no-sources")

    embedded = connected & set(EMBEDDED_PROVIDERS)
    # The probe runs while the chart classifier's call is in flight; a chart or
    # named-repo answer discards it.
    probe = None
    if embedded and not (len(embedded) == 1 and "github" not in connected):
        probe = _ROUTING_POOL.submit(
            contextvars.copy_context().run,
            _probe_scores, question, org_id, workspace_id, connected,
        )
    # Beside the probe, not after it: a SQL lookup, no model call.
    rows = None
    if probe is not None and viewer is not None:
        rows = _ROUTING_POOL.submit(
            contextvars.copy_context().run,
            _rows_named_provider, question, org_id, workspace_id, viewer, embedded,
        )

    classified = _try_question_route(
        question, connected, org_id, workspace_id, live_out=live_out,
        chart_out=chart_out, chart_from_words=chart_from_words, viewer=viewer,
        context=context,
    )
    if classified is not None:
        return classified

    if "github" in connected:
        named = _named_repo(question, org_id, workspace_id)
        if named:
            logger.info("Agent routing: %r names repo %s", question[:60], named)
            return RoutingDecision("github", "repo-named")

    if len(embedded) == 1 and "github" not in connected:
        return RoutingDecision(next(iter(embedded)), "only-source")

    # The asker NAMED where to look: one named tool is routed there; several
    # are probed among themselves and the connected answer reads exactly those.
    named_tools = named_providers(question, embedded)
    if named_tools:
        if len(named_tools) == 1:
            return RoutingDecision(next(iter(named_tools)), "tool-named")
        scores = (
            probe.result() if probe is not None
            else _probe_scores(question, org_id, workspace_id, connected)
        )
        best_named = max(sorted(named_tools), key=lambda p: scores.get(p, 0.0))
        return RoutingDecision(best_named, "tools-named", scores)

    graph_tool = _graph_named_provider(graph_plan, embedded)
    if graph_tool is not None:
        logger.info("Agent routing: %r names a %s item (graph)", question[:60], graph_tool)
        return RoutingDecision(graph_tool, "graph-named")

    scores = (
        probe.result() if probe is not None
        else _probe_scores(question, org_id, workspace_id, connected)
    )
    threshold = RagSettings.from_env().similarity_threshold
    best = max(scores, key=scores.get) if scores else None

    # The question names a cell of one tool's sheet: that tool holds the rows a
    # sheet never embeds. A refusal there is retried (`refusal_retry`).
    rows_tool = rows.result() if rows is not None else None
    if rows_tool is not None and rows_tool != best and scores.get(rows_tool, 0.0) >= threshold:
        logger.info("Agent routing: %r names rows of a %s sheet", question[:60], rows_tool)
        return RoutingDecision(rows_tool, "rows-named", scores)

    if context and _no_clear_winner(scores):
        follow = _probe_scores(f"{context}\n{question}", org_id, workspace_id, connected)
        follow_best = max(follow, key=follow.get) if follow else None
        if follow_best is not None and follow[follow_best] >= threshold:
            return RoutingDecision(follow_best, "follow-up", follow)

    if best is not None and scores[best] >= threshold:
        return RoutingDecision(best, "best-match", scores)

    if "github" in connected and _CODE_INTENT.search(question):
        return RoutingDecision("github", "code-intent", scores)

    if best is not None:
        # Routes to the closest source even below the gate: its own gate refuses
        # honestly.
        return RoutingDecision(best, "weak-best-match", scores)

    if len(embedded) == 1:
        return RoutingDecision(next(iter(embedded)), "only-source", scores)
    if "github" in connected and not embedded:
        return RoutingDecision("github", "only-source", scores)
    return RoutingDecision(default_key, "no-sources", scores)
