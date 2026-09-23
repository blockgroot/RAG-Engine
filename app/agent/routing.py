"""Pick the agent for a question, so the member does not have to.

Before this, ``orchestration.route_agent_key`` only honoured an explicit
``requested_agent`` — the per-source TABS in the UI were the router. Asking
someone to know whether an answer lives in Notion or Slack before they ask is
asking them to already have the answer.

Why not an LLM for the source
-----------------------------
CLAUDE.md §3 commits to a deterministic router for *which corpus answers*,
and that commitment is kept here rather than amended, because a better
signal is already sitting in the database: **which provider's content
actually resembles the question**. One embedding of the question, one
grouped vector query, and the answer is a measurement rather than a guess.

Chart vs document is the one classifier: it selects a registry metric (or
qa), never a source by name. The metric's ``provider`` IS the connector.

Keyword routing was rejected for the same reason. "What did we decide about
pricing?" carries no keyword at all, and the whole point is that it routes to
whichever source actually holds pricing content.

GitHub is the structural exception
----------------------------------
GitHub embeds nothing (``app/githublive/``), so it has no chunks and can never
win a cosine race — it would be permanently unreachable under a purely
vector-based router. It gets two narrow, explainable signals instead: the
question naming one of its authorized repositories (strong — nothing else in
the org is called that), or code-shaped intent when no embedded provider can
clear the confidence gate (weak, and deliberately last).

What this does NOT do
---------------------
It never decides whether an answer exists — only which agent is asked. Every
grounding guarantee stays exactly where it was: the routed agent still runs
the confidence gate and the strict prompt, and still returns the fixed
fallback when its own content cannot answer. A misroute therefore costs a
refusal, never a wrong answer from the wrong source.
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

# The probe is a HINT, not the answer, and it now sits on the critical path of
# every question — so it gets a hard, short ceiling instead of the embedding
# provider's general one (EMBEDDING_TIMEOUT, default 60s). Without this a
# hanging remote embedder costs up to 60s BEFORE the pipeline starts, and the
# pipeline then has its own 60s: a two-minute wait to be handed a refusal,
# because a timed-out probe degrades to the default agent.
#
# 5s is generous for a single-string embed and still an order of magnitude
# below the point where a person assumes the product is broken.
DEFAULT_PROBE_TIMEOUT_SECONDS = 5.0

_probe_provider = None
_probe_lock = threading.Lock()


def _probe_embedder():
    """Cached embedder for the routing probe. Same MODEL, shorter timeout.

    Same model matters more than it looks: the probe's vector is compared
    against stored chunk embeddings, so a different model would make every
    cosine meaningless rather than merely wrong.

    Cached because ``RemoteEmbeddingProvider`` is deliberately uncached in the
    factory (it holds no weights) and constructs a fresh HTTP client each call
    — building one per question would throw away connection reuse on the
    critical path. The local backend needs none of this: no network, so no hang
    to bound, and its own singleton already prevents a second 500MB load.
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


#: Providers that have embedded content to score against. GitHub is absent by
#: construction, not by omission.
EMBEDDED_PROVIDERS = ("notion", "google", "slack", "linear")

#: Words that mean "this is about the codebase" strongly enough to reach for
#: GitHub when nothing embedded can answer. Deliberately narrow and free of
#: collisions: "issue" and "ticket" are Linear's language, "doc" and "page" are
#: Notion's, and "thread" is Slack's — a word that could belong to two sources
#: is worse than no word at all.
_CODE_INTENT = re.compile(
    r"\b("
    r"repo|repos|repositor(?:y|ies)"
    r"|commit|commits|committed"
    r"|pull request|pull requests|\bPRs?\b"
    r"|branch|branches|merged?"
    # Review vocabulary. Safe to add despite "review" appearing in documents
    # ("under review", "performance review") because code-intent is checked
    # AFTER the cosine probe: a document question that scores above the gate
    # already won. `reviewer`/`approved` are code-shaped enough to be worth the
    # remaining risk, and a misroute costs a refusal, not a wrong answer.
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
    #: Short machine-readable cause: ``"only-source"``, ``"repo-named"``,
    #: ``"best-match"``, ``"code-intent"``, ``"weak-best-match"``,
    #: ``"no-sources"``, or ``"requested"`` when the caller pinned one.
    reason: str
    #: provider -> best cosine seen. Empty when no probe ran. A diagnostic:
    #: routing quality is not observable without it, and CLAUDE.md's standing
    #: advice on the 0.35 gate is to validate against real logged scores.
    scores: dict[str, float] = field(default_factory=dict)
    chart_spec: object | None = None
    chart_refusal: str | None = None


#: What a member is likely to CALL each service when they type its name.
#: Deliberately tight -- an alias that can appear innocently in a sentence would
#: hijack the routing ("repo" is not a GitHub alias, "docs" not a Drive one).
#: Lives here rather than in ``api/schedulers`` (its first caller) because the
#: scheduler's provider pick and a DM's SCOPE pick must read the same names: a
#: word strong enough to choose a service is strong enough to choose the space
#: that service is connected in.
PROVIDER_ALIASES: dict[str, tuple[str, ...]] = {
    "github": ("github",),
    "slack": ("slack",),
    "linear": ("linear",),
    "notion": ("notion",),
    "google": ("google drive", "google", "drive", "gdrive"),
}


def named_provider(text: str, available) -> str | None:
    """The service this text NAMES, when it names exactly one of them.

    Someone who writes "in github" has answered the question being asked, and a
    word match beats a similarity score at reading that intent. Two named
    services return None rather than guess: the wrong one is worse than
    falling through to the measurement.
    """
    lowered = (text or "").lower()
    hits = {
        provider
        for provider in available
        for alias in PROVIDER_ALIASES.get(provider, ())
        if re.search(rf"\b{re.escape(alias)}\b", lowered)
    }
    return hits.pop() if len(hits) == 1 else None


def _connected_providers(org_id: str, workspace_id: str | None) -> set[str]:
    """Providers connected IN THIS SCOPE. Never falls back to the org's.

    A space sees only its own connections (CLAUDE.md §3), so a space-scoped
    question must not be routed to a source that space cannot read — it would
    route to an agent that then correctly refuses, which reads as the product
    being broken rather than the space being empty.
    """
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
    """The authorized repo this question names, if any. The shared matcher.

    Both ladder rungs that care about a repo name route through here: agent
    selection (``_named_repo``) and, for a Slack DM, SCOPE selection
    (``_named_scope``). They had to agree anyway -- a repo name strong enough
    to pick GitHub is strong enough to pick the space GitHub is connected in
    -- and one matcher is what makes that true by construction.

    Matched against AUTHORIZED repos only, so this cannot be steered by a
    question mentioning some public repository. Both ``owner/name`` and the
    bare ``name`` count: people say "Chain-Guard", not "18-sana/Chain-Guard".
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


#: ``probe_best_scope`` found nothing indexed in any of the caller's scopes.
#: A distinct object because ``None`` means "org-wide", a real answer.
_NO_MATCH = object()


def _probe_scores(
    question: str,
    org_id: str,
    workspace_id: str | None,
    candidates: set[str],
) -> dict[str, float]:
    """Best cosine similarity per provider for this question. One query.

    Grouped in SQL rather than one query per provider: the providers share an
    index and a scan, and N round trips to rank N sources is the kind of
    per-item fan-out this codebase counts rather than times (CLAUDE.md §5).

    ``org_id`` and ``workspace_id`` are both in the WHERE clause, so this
    probe is under the same isolation as retrieval itself — a routing decision
    must never be informed by content the asker cannot read.
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

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT d.source_provider,
                   MAX(1 - (c.embedding <=> %s::vector)) AS best
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
    """Lowercased alphanumeric tokens, crudely singularised.

    `Meeting_notes_1` and "Meeting_note_1" must match: people retype a title
    from memory, and an exact-string check fails on one letter.
    """
    return {
        w[:-1] if len(w) > 3 and w.endswith("s") else w
        for w in re.findall(r"[a-z0-9]+", text.lower())
    }



def _connection_scopes(org_id: str, scope_ids: list[str | None]):
    """``(workspace_id, provider, repos)`` for every live connection in these scopes.

    One query for both rungs that need it. GitHub is the reason it exists --
    it has no chunks, so it is invisible to every other signal in scope
    selection -- but a connector the asker NAMES is the same kind of evidence
    whichever one it is, so the read is not GitHub-specific.
    """
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

    The exact counterpart of rung 7 in ``choose_agent``, and ordered the same
    way for the same reason: BELOW the probe, so a document question that
    actually scores is never hijacked by a code-shaped word inside it
    ("who approved the budget?"), and above the weak fall-through, so a scope
    whose only source embeds nothing is reachable at all.

    Exactly one candidate or nothing: with two GitHub-bearing scopes the
    question names neither, and the wrong space is worse than the probe.
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

    Ordered ahead of the cosine probe for the reason `_named_repo` already is
    (CLAUDE.md §3): a document ABOUT meeting notes can out-score the meeting
    notes themselves, so a name the asker actually typed is stronger evidence
    than a similarity score. Measured: "What should I know from Meeting_note_1?"
    probed to org-wide and answered from Linear, while the file sat indexed in
    the Meeting notes space one scope away.

    Requires EVERY token of the name to appear in the question, so a one-word
    title cannot hijack an unrelated question; two scopes matching resolves to
    NEITHER -- the wrong space is worse than letting the probe decide.
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

    # An authorized REPO NAME is a name for this scope too, and it has to be
    # checked here or GitHub is unreachable from a DM by construction. Every
    # other signal in this function comes from `chunks`/`documents`, and
    # GitHub embeds nothing -- so a space whose GitHub holds `Chain-Guard`
    # looks empty to `probe_best_scope`, the probe picks whichever scope has
    # the most prose, and `choose_agent` then runs in a scope with no GitHub
    # connection at all, where every GitHub rung is guarded on
    # `"github" in connected` and is skipped. Measured: "What does the
    # chain-guard repository do?" answered from org-wide Google Drive while
    # the repo sat authorized in a space one scope away.
    for scope_id, provider, repos in conn_rows:
        if provider == "github" and _repo_named_in(question, repos):
            candidates.add(scope_id)

    # NAMING THE CONNECTOR is a name too, and its absence was the defect this
    # rung fixes. "what were my contributions in github?" reached org-wide
    # Company -- the probe clears 0.35 there for almost any question, since the
    # company corpus is the largest one a member can see -- and the GitHub-
    # bearing space one scope away was never considered. `_code_scope` could
    # not save it: that rung sits BELOW the probe on purpose, so a scope that
    # scores always pre-empts it. A typed connector name is high-precision
    # enough to belong up here with a repo name, and it is the ONLY signal a
    # source with no corpus can offer when no repo is named.
    #
    # Exactly one connector named, or nothing: two services named picks
    # neither, the rule this whole function already follows.
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
    """Which of the caller's scopes should answer. The Slack DM entry point.

    The counterpart to ``choose_agent``, and deliberately the same SHAPE: a
    deterministic precedence ladder, a name beating a measurement, and a
    fall-through that never fails the question. Agent selection INSIDE the
    chosen scope is then `choose_agent` + the LangGraph exactly as the web
    chat runs it -- there is one router, not a Slack copy of one.

    Only Slack needs this. In the app the member is already standing in a
    space or in company Ask, so the UI supplies the scope; a DM has no such
    context and is the one surface that must infer it.

    Precedence, mirroring ``choose_agent`` rung for rung:

    1. **A NAME the asker typed** -- a space name, a document title, or an
       authorized repo. Stronger evidence than a similarity score, because a
       document ABOUT a thing routinely out-scores the thing itself.
    2. **The best-scoring scope, if it clears the confidence gate.** The same
       0.35 retrieval already uses: below it, nothing in that scope resembles
       the question.
    3. **Code intent, when nothing cleared the gate.** A scope whose only
       source is GitHub has no chunks and can never win step 2 -- it would be
       permanently unreachable from a DM without this. Below the probe so a
       code word inside a document question cannot hijack a scope that scores.
    4. **The best score anyway**, then ``_NO_MATCH``. The chosen scope's own
       gate still refuses honestly, so a miss costs a refusal, never an answer
       from the wrong space.

    Returns the winning ``workspace_id`` (``None`` = org-wide) or ``_NO_MATCH``.
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
    """``(workspace_id, best cosine)`` for the closest scope. ONE query.

    Used by the Slack DM path, where the asker is a person rather than a space:
    they legitimately see org-wide content AND every space they belong to, so a
    DM that could only read one of those is answering a narrower question than
    the one asked. The scopes passed in are the caller's OWN -- membership is
    resolved before this is called and is never inferred here.

    This does NOT blend scopes. It picks the single best one and the pipeline
    then runs inside it exactly as before, so a space's rows never mix with
    org-wide rows in one answer (CLAUDE.md §3: blending is what would make
    membership meaningless). Returning the winner rather than merging is also
    what keeps this one grouped query instead of one retrieval per scope.

    ``None`` when nothing is indexed in any scope. The SCORE is returned with
    the winner because the caller has to compare it against the confidence
    gate -- a scope that merely scored highest has not necessarily been
    resembled at all, and that difference is what lets a GitHub-only scope be
    reached below the gate instead of always losing to whichever scope holds
    the most prose.
    """
    if not workspace_ids:
        return None

    from ..db.connection import get_connection

    try:
        vector = _probe_embedder().embed([question])[0]
    except Exception:  # noqa: BLE001 - caller falls back to org-wide
        logger.warning("Slack DM routing: could not embed the question", exc_info=True)
        return None

    # `= ANY(array)` cannot match NULL (org-wide), so the org-wide scope is
    # asked for with its own IS NULL branch rather than being silently dropped.
    spaces = [w for w in workspace_ids if w is not None]
    include_org_wide = None in workspace_ids

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT c.workspace_id::text,
                   MAX(1 - (c.embedding <=> %s::vector)) AS best
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
    """Whether GitHub has anything to read in this scope.

    Deliberately checks AUTHORIZED REPOS, not ``activity_facts``: GitHubAgent
    reads live, so a freshly connected installation with no recorded facts can
    still answer perfectly well. Gating a live route on facts would block a
    real answer for up to a sync interval.

    An installation authorizing zero repos genuinely has nothing to say, and
    routing there produces a fallback that reads as the product being broken.
    """
    from ..auth.credentials import get_connection_config

    try:
        config = get_connection_config(org_id, "github", workspace_id) or {}
    except Exception:  # noqa: BLE001 - routing must never fail a question
        return False
    return bool(config.get("repos"))


def _try_insights_route(
    question: str,
    connected: set[str],
    org_id: str,
    workspace_id: str | None,
) -> RoutingDecision | None:
    """Chart vs document vs live GitHub, decided by the classifier.

    Returns None when this is ordinary Q&A so the cosine router still runs.
    Never raises: a dead classifier is a document question, not a failed Ask.
    """
    from ..insights import panels as panel_defs
    from ..insights.resolve import classify_question

    providers = [p for p in sorted(connected) if panel_defs.for_provider(p)]
    if not providers:
        return None
    try:
        intent = classify_question(question, providers=providers, fail_open=True)
    except Exception:  # noqa: BLE001
        logger.warning("Agent routing: chart classifier failed", exc_info=True)
        return None
    if intent.kind == "qa":
        return None
    if intent.kind == "github_live":
        # The one place a model picks a non-chart destination. GitHub embeds
        # nothing, so it can never win the cosine probe, and `_CODE_INTENT`
        # cannot be widened to cover the rest: no regex separates "auth code"
        # from "code of conduct". `_CODE_INTENT` STAYS as the floor below --
        # this classifier fails open, so it must be additive, never the only
        # door in.
        if not _has_authorized_repos(org_id, workspace_id):
            logger.info(
                "Agent routing: classified code question but no authorized "
                "repos for org %s", org_id,
            )
            return None
        logger.info(
            "Agent routing: %r classified as a live GitHub question",
            question[:60],
        )
        return RoutingDecision("github", "classified-code-question")
    if intent.kind == "chart" and intent.spec is not None:
        return RoutingDecision(
            INSIGHTS_KEY,
            "chart",
            chart_spec=intent.spec,
        )
    if intent.kind == "refuse":
        return RoutingDecision(
            INSIGHTS_KEY,
            "chart-refuse",
            chart_refusal=intent.message,
        )
    return None


def choose_agent(
    question: str,
    org_id: str,
    *,
    workspace_id: str | None = None,
    requested_agent: str | None = None,
) -> RoutingDecision:
    """Decide which agent answers ``question``. Never raises.

    Precedence, and the reason for each step:

    1. **An explicit request wins.** The API still accepts ``agent``, so an
       existing caller (and every test that pins a source) keeps working.
    2. **A countable visual**, when the classifier (not a keyword list) says
       this is a chart and names a registry metric. The metric's provider IS
       the connector — InsightsAgent never blends corpora. A visual we cannot
       count is still routed here so RAG cannot invent a number.
    3. **A classified live-code question**, when the classifier says the answer
       is in the code itself rather than in a document. The one place a model
       picks a non-chart destination, and only ever GitHub — which has no
       corpus, so the cosine probe cannot reach it. Offered only when GitHub is
       connected, and a resolvable metric (step 2) still wins.
    4. **A named repository wins.** Nothing else in the org is called that, so
       it is the least ambiguous signal available — and it must beat the vector
       probe, because a Notion page *about* a repo would otherwise outscore the
       repo itself.
    5. **One embedded source ⇒ no probe.** Saves an embedding and a query in
       the common single-source tenant.
    6. **Otherwise, the best-scoring provider**, if it clears the confidence
       gate. Same threshold retrieval already uses, for the same reason: below
       it, nothing here resembles the question.
    7. **Code intent (the keyword floor), when nothing embedded cleared the
       gate.** Kept below the probe so a code word inside a document question
       cannot hijack it, and kept at all because step 3 fails OPEN — a dead
       classifier must not make GitHub unreachable.
    8. **The best score anyway**, even below the gate — the routed agent's own
       gate will refuse honestly, which is a better outcome than routing to a
       default agent that never had the content.
    9. **The pre-existing default** (workspace agent inside a space, else the
       legacy policy agent) when there is nothing to route to at all.
    """
    if requested_agent in DIRECT_AGENT_KEYS or requested_agent == INSIGHTS_KEY:
        return RoutingDecision(requested_agent, "requested")

    default_key = WORKSPACE_KEY if workspace_id is not None else POLICY_KEY

    try:
        connected = _connected_providers(org_id, workspace_id)
    except Exception:  # noqa: BLE001 - routing must never fail a question
        logger.warning("Agent routing: could not list connections", exc_info=True)
        return RoutingDecision(default_key, "no-sources")

    if not connected:
        return RoutingDecision(default_key, "no-sources")

    embedded = connected & set(EMBEDDED_PROVIDERS)
    # The cosine probe (an embedding + one query) runs WHILE the chart
    # classifier's model call is in flight rather than after it -- the two are
    # independent, and serially they were the whole of routing's latency. A
    # chart or named-repo answer simply discards it.
    probe = None
    if embedded and not (len(embedded) == 1 and "github" not in connected):
        probe = _ROUTING_POOL.submit(
            contextvars.copy_context().run,
            _probe_scores, question, org_id, workspace_id, connected,
        )

    visual = _try_insights_route(question, connected, org_id, workspace_id)
    if visual is not None:
        return visual

    if "github" in connected:
        named = _named_repo(question, org_id, workspace_id)
        if named:
            logger.info("Agent routing: %r names repo %s", question[:60], named)
            return RoutingDecision("github", "repo-named")

    if len(embedded) == 1 and "github" not in connected:
        return RoutingDecision(next(iter(embedded)), "only-source")

    scores = (
        probe.result() if probe is not None
        else _probe_scores(question, org_id, workspace_id, connected)
    )
    threshold = RagSettings.from_env().similarity_threshold
    best = max(scores, key=scores.get) if scores else None

    if best is not None and scores[best] >= threshold:
        return RoutingDecision(best, "best-match", scores)

    if "github" in connected and _CODE_INTENT.search(question):
        return RoutingDecision("github", "code-intent", scores)

    if best is not None:
        # Deliberately routes to a source that probably cannot answer: its gate
        # then produces the honest fallback, with citations from the closest
        # source rather than from an agent that was never asked.
        return RoutingDecision(best, "weak-best-match", scores)

    if len(embedded) == 1:
        return RoutingDecision(next(iter(embedded)), "only-source", scores)
    if "github" in connected and not embedded:
        return RoutingDecision("github", "only-source", scores)
    return RoutingDecision(default_key, "no-sources", scores)
