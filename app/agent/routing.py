"""Pick the agent for a question, so the member does not have to.

Before this, ``orchestration.route_agent_key`` only honoured an explicit
``requested_agent`` — the per-source TABS in the UI were the router. Asking
someone to know whether an answer lives in Notion or Slack before they ask is
asking them to already have the answer.

Why not an LLM
--------------
CLAUDE.md §3 commits to a deterministic router, and that commitment is kept
here rather than amended, because a better signal is already sitting in the
database: **which provider's content actually resembles the question**. One
embedding of the question, one grouped vector query, and the answer is a
measurement rather than a guess. An LLM classifier would add a call, a quota
draw (15 rpm — see ``app/llm/pacing.py``), a latency floor, and a new failure
mode, to decide something the corpus can answer directly.

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

import logging
import re
from dataclasses import dataclass, field

from ..config.settings import RagSettings
from .orchestration import DIRECT_AGENT_KEYS, POLICY_KEY, WORKSPACE_KEY

logger = logging.getLogger(__name__)

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
    r"|codebase|source code"
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


def _named_repo(question: str, org_id: str, workspace_id: str | None) -> str | None:
    """A GitHub repo this question names by name, if any.

    Matched against the installation's AUTHORIZED repos only, so this cannot
    be steered by a question mentioning some public repository. Both
    ``owner/name`` and the bare ``name`` count: people say "Chain-Guard", not
    "18-sana/Chain-Guard".
    """
    from ..auth.credentials import get_connection_config

    try:
        config = get_connection_config(org_id, "github", workspace_id) or {}
    except Exception:  # noqa: BLE001 - routing must never fail a question
        return None

    lowered = question.lower()
    for repo in config.get("repos") or []:
        full = str(repo.get("full_name") or "")
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
    from ..embeddings import build_embedding_provider

    try:
        vector = build_embedding_provider().embed([question])[0]
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
    2. **A named repository wins.** Nothing else in the org is called that, so
       it is the least ambiguous signal available — and it must beat the vector
       probe, because a Notion page *about* a repo would otherwise outscore the
       repo itself.
    3. **One embedded source ⇒ no probe.** Saves an embedding and a query in
       the common single-source tenant.
    4. **Otherwise, the best-scoring provider**, if it clears the confidence
       gate. Same threshold retrieval already uses, for the same reason: below
       it, nothing here resembles the question.
    5. **Code intent, when nothing embedded cleared the gate.** Last, and only
       then, so a code word inside a document question cannot hijack it.
    6. **The best score anyway**, even below the gate — the routed agent's own
       gate will refuse honestly, which is a better outcome than routing to a
       default agent that never had the content.
    7. **The pre-existing default** (workspace agent inside a space, else the
       legacy policy agent) when there is nothing to route to at all.
    """
    if requested_agent in DIRECT_AGENT_KEYS:
        return RoutingDecision(requested_agent, "requested")

    default_key = WORKSPACE_KEY if workspace_id is not None else POLICY_KEY

    try:
        connected = _connected_providers(org_id, workspace_id)
    except Exception:  # noqa: BLE001 - routing must never fail a question
        logger.warning("Agent routing: could not list connections", exc_info=True)
        return RoutingDecision(default_key, "no-sources")

    if not connected:
        return RoutingDecision(default_key, "no-sources")

    if "github" in connected:
        named = _named_repo(question, org_id, workspace_id)
        if named:
            logger.info("Agent routing: %r names repo %s", question[:60], named)
            return RoutingDecision("github", "repo-named")

    embedded = connected & set(EMBEDDED_PROVIDERS)
    if len(embedded) == 1 and "github" not in connected:
        return RoutingDecision(next(iter(embedded)), "only-source")

    scores = _probe_scores(question, org_id, workspace_id, connected)
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
