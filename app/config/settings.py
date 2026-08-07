"""Typed configuration, read from environment variables in one place.

Nothing else in the app calls ``os.getenv`` for provider config — factories take
these settings objects, so there is a single, documented source of truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_TIMEOUT = 60.0
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_EMBEDDING_BACKEND = "local"

# Output dimension of the current embedding model (BGE-M3 = 1024). The DB schema
# declares vector(EMBEDDING_DIM); the two MUST stay in sync — see CLAUDE.md.
DEFAULT_EMBEDDING_DIM = 1024

# Chunking defaults (tokens, BGE-M3 tokenizer). See app/ingestion/chunking.py.
DEFAULT_CHUNK_SIZE = 256
DEFAULT_CHUNK_OVERLAP = 40

DEFAULT_VECTOR_STORE_BACKEND = "pgvector"

# RAG query-path defaults. See app/rag/pipeline.py for the reasoning behind the
# similarity threshold value and the two-layer grounding design.
DEFAULT_RAG_TOP_K = 5
DEFAULT_RAG_SIMILARITY_THRESHOLD = 0.35
# Cap chars of retrieved context fed into the grounded prompt (latency).
DEFAULT_RAG_MAX_CONTEXT_CHARS = 6000
# Cap completion length for answer generation (latency on slow free endpoints).
DEFAULT_RAG_MAX_ANSWER_TOKENS = 700
# How long a follow-up may wait for the previous turn's summary fold.
DEFAULT_MEMORY_FOLD_WAIT_SECONDS = 2.0
DEFAULT_RAG_FALLBACK_RESPONSE = (
    "I don't have information on that in the available policy documents."
)
# WorkspaceAgent's own fallback copy (Workspace-within-a-Workspace agent split)
# — a separate RagPipeline instance from the policy one, so it gets its own
# fixed fallback string rather than reusing the policy-flavored one above.
DEFAULT_WORKSPACE_FALLBACK_RESPONSE = (
    "I don't have anything about that in this workspace's connected content."
)

# The GitHub agent's refusal. Distinct copy because the *reason* differs: there
# is no retrieval here, so a refusal means "no tool could supply evidence for
# this", not "nothing matched in the corpus" — and the actionable next step for
# the user is different too (name a repo, or check it's in the installation).
DEFAULT_GITHUB_FALLBACK_RESPONSE = (
    "I couldn't find that in the connected GitHub repositories. Try naming the "
    "repository, or check that it's included in this organization's GitHub "
    "installation."
)

# Connection-pool sizing for the Postgres backing store.
DEFAULT_DB_POOL_MIN_SIZE = 1
DEFAULT_DB_POOL_MAX_SIZE = 10
DEFAULT_KEYWORD_CANDIDATE_LIMIT = 2000   # rows BM25 may rank for one query

# External content sources (Phase 4). Only "notion" exists so far.
DEFAULT_SOURCE_TYPE = "notion"

# Conversation memory (Phase 5, revised Phase 8). A "turn" is one question + its
# answer. ``recent_turns`` is the size of the verbatim window kept in full; every
# turn that falls out of that window is *incrementally* folded into a running
# summary (Phase 8 — no bulk threshold). See app/rag/pipeline.py for the reasoning
# behind the window size.
DEFAULT_MEMORY_RECENT_TURNS = 3

# Retrieval improvements (Phase 6): contextual retrieval (ingest-time),
# hybrid search + cross-encoder reranking (query-time). See app/rag/retrieval.py
# and CLAUDE.md §2/§4 for the reasoning behind each value.
DEFAULT_CONTEXTUAL_ENABLED = True          # prepend LLM context to each chunk at ingest
DEFAULT_CONTEXTUAL_CONCURRENCY = 8         # parallel context calls per document
DEFAULT_RETRIEVAL_HYBRID_ENABLED = True    # fuse vector + keyword (BM25-style) search
DEFAULT_RETRIEVAL_RERANK_ENABLED = True    # cross-encoder rerank of the candidate pool
DEFAULT_RETRIEVAL_CANDIDATE_POOL = 16      # how many candidates to fetch/rerank before top_k
DEFAULT_RETRIEVAL_RRF_K = 60               # Reciprocal Rank Fusion constant (standard default)
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

# Retrieval reuse across conversation turns (Phase 8). Before retrieval runs, the
# rewritten question's embedding is compared (plain cosine, NO LLM) against the
# previous turn's retrieved chunks; if the best similarity clears the threshold we
# reuse those chunks and skip a fresh retrieval. Threshold reasoning: CLAUDE.md §4.
DEFAULT_RETRIEVAL_REUSE_ENABLED = True
DEFAULT_RETRIEVAL_REUSE_THRESHOLD = 0.72

# Bounded retrieval recovery (Retrieval Discovery Gap). First retrieve stays as
# today; at most one optional recovery when evidence looks insufficient.
DEFAULT_RECOVERY_ENABLED = True
DEFAULT_RECOVERY_MAX_QUERIES = 2

# Compound-question decomposition (Phase 18). Heuristic gate first; LLM only when
# the question likely bundles multiple distinct asks.
DEFAULT_DECOMPOSE_ENABLED = True

# Request-level time budget (Phase 19). Optional stages check remaining time before starting.
DEFAULT_REQUEST_DEADLINE_SECONDS = 45.0
DEFAULT_BUDGET_MIN_STAGE_SECONDS = 3.0

# Query→answer cache for standalone questions (Phase 19).
DEFAULT_QUERY_CACHE_ENABLED = True
DEFAULT_QUERY_CACHE_TTL_SECONDS = 300

# API rate limiting (Phase 21) — chat/query endpoint.
DEFAULT_RATE_LIMIT_ENABLED = True
DEFAULT_RATE_LIMIT_CHAT_REQUESTS = 30
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60

# Ingestion sanitization (Phase 21).
DEFAULT_INGEST_MAX_DOCUMENT_CHARS = 2_000_000
DEFAULT_INGEST_MAX_CONTROL_CHAR_RATIO = 0.05

# Lightweight query spelling/normalization (Phase 17). Corpus-vocab
# SymSpell — no LLM on the happy path. Kill-switch: QUERY_NORM_ENABLED=false.
DEFAULT_QUERY_NORM_ENABLED = True
DEFAULT_QUERY_NORM_MAX_EDIT_DISTANCE = 1
DEFAULT_QUERY_NORM_MIN_WORD_LENGTH = 4

# Web search tool (Phase 5). Keyless DuckDuckGo by default (no paid dependency);
# set WEB_SEARCH_PROVIDER=tavily + WEB_SEARCH_API_KEY for production quality.
DEFAULT_WEB_SEARCH_ENABLED = True
DEFAULT_WEB_SEARCH_PROVIDER = "duckduckgo"
DEFAULT_WEB_SEARCH_MAX_RESULTS = 5
DEFAULT_WEB_SEARCH_TIMEOUT = 8.0

# Auth / session (Phase 10). Session and magic-link TTLs are minutes.
# Session default is long-lived (30 days) rather than a typical short web
# session: this is a low-risk internal tool (not banking/finance), the cookie
# is already httpOnly+Secure+SameSite=Lax, and there's no refresh-token flow
# to silently keep a short-lived session alive — so the TTL itself has to be
# the thing that keeps a user logged in across normal day-to-day use instead
# of re-requesting a magic link every hour. Revisit with a proper refresh
# mechanism if the risk profile changes (e.g. more sensitive data, external
# users).
DEFAULT_SESSION_TTL_MINUTES = 60 * 24 * 30  # 30 days
DEFAULT_MAGIC_LINK_TTL_MINUTES = 10
DEFAULT_SIGNUP_ACTION_TTL_HOURS = 72  # 3 days

# HTTP API (Phase 10+).
DEFAULT_API_HOST = "0.0.0.0"
DEFAULT_API_PORT = 8000

# Outbound email for magic links (Phase 10). "console" (default, dev) prints the
# link instead of sending it — no external dependency required to run locally.
DEFAULT_EMAIL_SENDER = "console"


def _env_positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value > 0 else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class LLMSettings:
    """Configuration for the LLM provider.

    - ``model``    provider/model string, e.g. ``openai/auto`` or
      ``anthropic/claude-sonnet-5`` (required)
    - ``aux_model`` optional cheaper/faster model for rewrite, decompose,
      recovery expansion, summarization, and ingest contextualization (Phase 19).
      When unset, every stage uses ``model``.
    - ``api_key``  optional explicit key (else provider's standard env var)
    - ``base_url`` optional; for OpenAI-compatible / self-hosted endpoints
    """

    model: str | None
    aux_model: str | None
    api_key: str | None
    base_url: str | None
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "LLMSettings":
        return cls(
            model=os.getenv("LLM_MODEL"),
            aux_model=os.getenv("LLM_AUX_MODEL") or None,
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
            timeout=float(os.getenv("LLM_TIMEOUT") or DEFAULT_TIMEOUT),
        )


@dataclass(frozen=True)
class EmbeddingSettings:
    """Configuration for the embedding provider.

    - ``backend``  ``local`` (in-process sentence-transformers) or ``remote``
      (HTTP OpenAI-compatible endpoint)
    - ``model``    embedding model id
    - ``device``   optional device for the local backend (cpu/cuda/mps)
    - ``api_key`` / ``base_url``  used only by the remote backend
    """

    backend: str
    model: str | None
    device: str | None
    api_key: str | None
    base_url: str | None
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "EmbeddingSettings":
        return cls(
            backend=(os.getenv("EMBEDDING_BACKEND") or DEFAULT_EMBEDDING_BACKEND).lower(),
            model=os.getenv("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL,
            device=os.getenv("EMBEDDING_DEVICE") or None,
            api_key=os.getenv("EMBEDDING_API_KEY"),
            base_url=os.getenv("EMBEDDING_BASE_URL"),
            timeout=float(os.getenv("EMBEDDING_TIMEOUT") or DEFAULT_TIMEOUT),
        )


@dataclass(frozen=True)
class DatabaseSettings:
    """Configuration for the Postgres/pgvector backing store.

    - ``url``  standard libpq connection string, e.g.
      ``postgresql://user:pass@host:5432/dbname``
    - ``embedding_dim``  vector dimension the ``chunks.embedding`` column uses;
      must match the embedding model's output (BGE-M3 = 1024)
    """

    url: str | None
    embedding_dim: int = DEFAULT_EMBEDDING_DIM
    pool_min_size: int = DEFAULT_DB_POOL_MIN_SIZE
    pool_max_size: int = DEFAULT_DB_POOL_MAX_SIZE
    # Ceiling on rows the keyword (BM25) search pulls back for one query. The
    # query was previously unbounded, so a common term's cost grew with the
    # corpus. Set high enough to be a no-op at realistic sizes; see
    # ``PgVectorStore.keyword_search`` for what changes past it.
    keyword_candidate_limit: int = DEFAULT_KEYWORD_CANDIDATE_LIMIT

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        return cls(
            url=os.getenv("DATABASE_URL"),
            embedding_dim=int(os.getenv("EMBEDDING_DIM") or DEFAULT_EMBEDDING_DIM),
            pool_min_size=int(os.getenv("DB_POOL_MIN_SIZE") or DEFAULT_DB_POOL_MIN_SIZE),
            pool_max_size=int(os.getenv("DB_POOL_MAX_SIZE") or DEFAULT_DB_POOL_MAX_SIZE),
            keyword_candidate_limit=_env_positive_int(
                "KEYWORD_CANDIDATE_LIMIT", DEFAULT_KEYWORD_CANDIDATE_LIMIT
            ),
        )


@dataclass(frozen=True)
class ChunkingSettings:
    """Configuration for document chunking (sizes measured in tokens)."""

    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP

    @classmethod
    def from_env(cls) -> "ChunkingSettings":
        return cls(
            chunk_size=int(os.getenv("CHUNK_SIZE") or DEFAULT_CHUNK_SIZE),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP") or DEFAULT_CHUNK_OVERLAP),
        )


@dataclass(frozen=True)
class VectorStoreSettings:
    """Configuration for the vector store abstraction layer."""

    backend: str = DEFAULT_VECTOR_STORE_BACKEND

    @classmethod
    def from_env(cls) -> "VectorStoreSettings":
        return cls(
            backend=(os.getenv("VECTOR_STORE_BACKEND") or DEFAULT_VECTOR_STORE_BACKEND).lower(),
        )


@dataclass(frozen=True)
class RagSettings:
    """Configuration for the RAG query path (retrieve -> gate -> generate).

    - ``top_k``  how many chunks to retrieve per question.
    - ``similarity_threshold``  minimum cosine similarity (in [0, 1]) the *best*
      retrieved chunk must clear before the LLM is called at all. Below it, we
      short-circuit to ``fallback_response`` and never invoke the model. This is
      the cheap first line of defence against answering from irrelevant context;
      the strict prompt (see ``app/rag/prompts.py``) is the second. Reasoning for
      the default value lives in ``app/rag/pipeline.py``.
    - ``fallback_response``  the single, fixed "I don't know" string. It is used
      in three places that MUST agree: the confidence gate, the LLM's refusal
      instruction, and the pipeline's refusal detection — so it lives here as one
      source of truth rather than being duplicated.
    - ``max_context_chars``  total characters of retrieved chunk text placed in
      the grounded prompt (most-relevant first; later chunks truncated/dropped).
      Keeps prompt tokens bounded so slow endpoints stay usable.
    - ``max_answer_tokens``  completion cap for generate / tone-retry calls
      (``None`` / 0 = provider default). Shorter caps finish faster on free LLMs.
    """

    top_k: int = DEFAULT_RAG_TOP_K
    similarity_threshold: float = DEFAULT_RAG_SIMILARITY_THRESHOLD
    fallback_response: str = DEFAULT_RAG_FALLBACK_RESPONSE
    max_context_chars: int = DEFAULT_RAG_MAX_CONTEXT_CHARS
    max_answer_tokens: int | None = DEFAULT_RAG_MAX_ANSWER_TOKENS

    @classmethod
    def from_env(cls) -> "RagSettings":
        raw_answer_tokens = os.getenv("RAG_MAX_ANSWER_TOKENS")
        if raw_answer_tokens is None:
            max_answer_tokens: int | None = DEFAULT_RAG_MAX_ANSWER_TOKENS
        elif raw_answer_tokens.strip() in ("", "0"):
            max_answer_tokens = None
        else:
            max_answer_tokens = int(raw_answer_tokens)
        return cls(
            top_k=int(os.getenv("RAG_TOP_K") or DEFAULT_RAG_TOP_K),
            similarity_threshold=float(
                os.getenv("RAG_SIMILARITY_THRESHOLD") or DEFAULT_RAG_SIMILARITY_THRESHOLD
            ),
            fallback_response=os.getenv("RAG_FALLBACK_RESPONSE") or DEFAULT_RAG_FALLBACK_RESPONSE,
            max_context_chars=int(
                os.getenv("RAG_MAX_CONTEXT_CHARS") or DEFAULT_RAG_MAX_CONTEXT_CHARS
            ),
            max_answer_tokens=max_answer_tokens,
        )


@dataclass(frozen=True)
class WorkspaceAgentSettings:
    """Configuration specific to ``WorkspaceAgent`` (Workspace-within-a-Workspace).

    ``WorkspaceAgent`` reuses the same ``RagPipeline`` machinery as
    ``PolicyAgent`` (gate, retrieval, tone-compliance) via a separate pipeline
    instance with a different ``PromptProfile`` (see ``app/rag/prompts.py``)
    — everything else (``top_k``, ``similarity_threshold``, etc.) comes from
    the same ``RagSettings.from_env()`` shared with the policy pipeline. Only
    the fixed fallback string is distinct, since it must be exact-string-matched
    consistently within ONE pipeline (gate/prompt/refusal-detection), and this
    is a second, independent pipeline instance.

    - ``fallback_response``  the single, fixed "I don't know" string for a
      workspace-scoped question. Same three-consumer-agreement discipline as
      ``RagSettings.fallback_response``, just a separate string for a separate
      pipeline.
    """

    fallback_response: str = DEFAULT_WORKSPACE_FALLBACK_RESPONSE

    @classmethod
    def from_env(cls) -> "WorkspaceAgentSettings":
        return cls(
            fallback_response=os.getenv("WORKSPACE_FALLBACK_RESPONSE")
            or DEFAULT_WORKSPACE_FALLBACK_RESPONSE,
        )


@dataclass(frozen=True)
class NotionSettings:
    """Configuration for the Notion content source (Phase 4, per-org since Phase 9).

    - ``token``  the *default* / legacy single integration secret (``NOTION_TOKEN``).
      Still used when an ingestion run names no specific org token — e.g. the single
      Phase 4 test org. For this phase auth is a Notion *internal integration
      secret* (a static token), the simplest viable auth given there is no web app
      yet to host an OAuth consent redirect.
    - ``tokens``  a name→secret map of *per-organization* integration secrets, one
      per real org, discovered generically from every ``NOTION_TOKEN_<NAME>`` env
      var (Phase 9). Nothing hardcodes how many orgs exist or their names — adding
      an org later is one new env var, no code change. Each org gets its OWN Notion
      integration, so an org's token can only see pages shared with *that*
      integration: the tenant boundary is enforced by Notion itself, not just our
      code. This is the static-token stand-in for real per-customer OAuth later.
    - ``client_id`` / ``client_secret`` / ``redirect_uri``  OAuth app credentials,
      read here so they aren't hardcoded and are ready for the later multi-tenant
      OAuth phase. They are NOT used by the adapter yet.
    """

    token: str | None
    tokens: dict[str, str] = field(default_factory=dict)
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None

    _TOKEN_PREFIX = "NOTION_TOKEN_"

    @classmethod
    def from_env(cls) -> "NotionSettings":
        # Discover per-org tokens generically: every NOTION_TOKEN_<NAME> becomes
        # an entry keyed by <name> (lower-cased), so orgs are data, not code.
        tokens = {
            key[len(cls._TOKEN_PREFIX):].lower(): value
            for key, value in os.environ.items()
            if key.startswith(cls._TOKEN_PREFIX)
            and len(key) > len(cls._TOKEN_PREFIX)
            and value
        }
        return cls(
            token=os.getenv("NOTION_TOKEN"),
            tokens=tokens,
            client_id=os.getenv("NOTION_CLIENT_ID"),
            client_secret=os.getenv("NOTION_CLIENT_SECRET"),
            redirect_uri=os.getenv("NOTION_REDIRECT_URI"),
        )

    def resolve_token(self, name: str | None = None) -> str:
        """Return the integration secret for ``name`` (or the default token).

        With a ``name`` we return *only* that org's token — never silently falling
        back to another org's or the global one, so an ingestion run can't cross a
        tenant boundary by accident. Without a name we use the default
        ``NOTION_TOKEN``. Raises ``ConfigurationError`` if the requested token is
        not configured.
        """
        from ..core.exceptions import ConfigurationError

        if name:
            token = self.tokens.get(name.lower())
            if not token:
                available = ", ".join(sorted(self.tokens)) or "(none configured)"
                raise ConfigurationError(
                    f"No Notion token named {name!r}. Set NOTION_TOKEN_{name.upper()} "
                    f"in your .env. Configured org tokens: {available}."
                )
            return token
        if self.token:
            return self.token
        raise ConfigurationError(
            "No Notion token configured. Set NOTION_TOKEN (default) or a per-org "
            "NOTION_TOKEN_<NAME> and pass its name to the ingestion run."
        )


DEFAULT_GOOGLE_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/documents.readonly"
)


@dataclass(frozen=True)
class GoogleSettings:
    """Configuration for the Google OAuth "Connect" flow (Google Drive/Docs).

    Unlike ``NotionSettings`` there is no legacy static-token path here — per
    Google Integration Plan decision D3, Google is OAuth-only from the start
    (no env-var-token fallback), so this is just client credentials + scopes.
    ``scopes`` is a single space-delimited string (Google's own convention for
    the ``scope`` request parameter), read as one env var rather than a list so
    parsing stays in one place (the provider, at request-build time).
    """

    client_id: str | None
    client_secret: str | None
    redirect_uri: str | None
    scopes: str = DEFAULT_GOOGLE_OAUTH_SCOPES

    @classmethod
    def from_env(cls) -> "GoogleSettings":
        return cls(
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            redirect_uri=os.getenv("GOOGLE_REDIRECT_URI"),
            scopes=os.getenv("GOOGLE_OAUTH_SCOPES", DEFAULT_GOOGLE_OAUTH_SCOPES),
        )


@dataclass(frozen=True)
class GitHubSettings:
    """Configuration for the GitHub App "Connect" flow (GitHub Integration D1).

    Like ``GoogleSettings`` there is deliberately **no** static-token path: a
    GitHub App installed on the customer's GitHub organization is the only
    supported credential, because repo access is then granted (and enforced) by
    GitHub's own install screen rather than by a field in our database — the
    same externally-enforced tenant boundary that made per-org Notion secrets
    the right call (CLAUDE.md §2).

    ``private_key`` is new secret material: an RS256 PEM used *only* to sign
    the short-lived App JWT that mints installation tokens
    (``app/auth/github_app.py``). It is never logged and never leaves the
    process. ``app_slug`` is the App's URL slug, needed to build the install
    URL — GitHub's install page lives at ``/apps/<slug>/installations/new``,
    not at an OAuth authorize endpoint.
    """

    app_slug: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    private_key: str | None = None

    @classmethod
    def from_env(cls) -> "GitHubSettings":
        raw_key = os.getenv("GITHUB_APP_PRIVATE_KEY")
        return cls(
            app_slug=os.getenv("GITHUB_APP_SLUG"),
            client_id=os.getenv("GITHUB_CLIENT_ID"),
            client_secret=os.getenv("GITHUB_CLIENT_SECRET"),
            # Accept a ``\n``-escaped single-line value so a multi-line PEM
            # survives .env files and secret managers that only hold flat
            # strings. A real multi-line value passes through untouched.
            private_key=raw_key.replace("\\n", "\n") if raw_key else None,
        )


@dataclass(frozen=True)
class GitHubAgentSettings:
    """Configuration specific to ``GitHubAgent``.

    Only the fallback string, mirroring ``WorkspaceAgentSettings`` — but for a
    different reason. There, the string must stay consistent across one
    pipeline's gate/prompt/refusal-detection. Here there is no pipeline at all:
    the agent returns this string whenever no tool could supply evidence, so it
    has exactly one consumer and simply needs to be configurable.
    """

    fallback_response: str = DEFAULT_GITHUB_FALLBACK_RESPONSE
    # When the first answer declares its evidence insufficient (MODE: C), fetch
    # complementary evidence ONCE and regenerate. Bounded exactly like
    # RECOVERY_ENABLED on the RAG side: at most one extra round, never a loop
    # chasing better evidence. Added after a live question hit a repo whose
    # README was an unmodified project template -- recent commit subjects
    # described the project fine, but nothing went looking for them.
    evidence_recovery_enabled: bool = True
    recovery_commit_count: int = 10

    @classmethod
    def from_env(cls) -> "GitHubAgentSettings":
        return cls(
            fallback_response=os.getenv("GITHUB_FALLBACK_RESPONSE")
            or DEFAULT_GITHUB_FALLBACK_RESPONSE,
            evidence_recovery_enabled=os.getenv(
                "GITHUB_EVIDENCE_RECOVERY_ENABLED", "true"
            ).lower()
            != "false",
            recovery_commit_count=int(os.getenv("GITHUB_RECOVERY_COMMIT_COUNT", "10")),
        )


@dataclass(frozen=True)
class GitHubLiveSettings:
    """Bounds on live GitHub reads (GitHub Integration Plan Phase 5).

    Every value here exists to stop an unbounded payload reaching a prompt. That
    is not a tidiness concern: GitHub documents that a commit diff can span 300
    files per page up to 3 000 total and that "larger diffs may time out", and a
    README can be arbitrarily long. Feeding either in whole would blow the
    context window and, worse, could silently drop the part of the evidence the
    answer depended on. Truncation here is always *marked* so the model can see
    the evidence is partial (risk T6).

    Unlike the RAG path there is no cache behind these calls, so timeouts are the
    only thing standing between a slow GitHub and a slow answer (risk T8).
    """

    enabled: bool = True
    timeout: float = 10.0
    readme_max_bytes: int = 40_000
    patch_max_bytes: int = 4_000
    max_files_per_commit: int = 25
    max_commits: int = 20
    max_attempts: int = 3

    @classmethod
    def from_env(cls) -> "GitHubLiveSettings":
        return cls(
            enabled=os.getenv("GITHUB_LIVE_ENABLED", "true").lower() != "false",
            timeout=float(os.getenv("GITHUB_LIVE_TIMEOUT", "10.0")),
            readme_max_bytes=int(os.getenv("GITHUB_README_MAX_BYTES", "40000")),
            patch_max_bytes=int(os.getenv("GITHUB_PATCH_MAX_BYTES", "4000")),
            max_files_per_commit=int(os.getenv("GITHUB_MAX_FILES_PER_COMMIT", "25")),
            max_commits=int(os.getenv("GITHUB_MAX_COMMITS", "20")),
            max_attempts=int(os.getenv("GITHUB_LIVE_MAX_ATTEMPTS", "3")),
        )


@dataclass(frozen=True)
class MemorySettings:
    """Conversation-memory sizing (Phase 5, revised Phase 8).

    - ``recent_turns``  the verbatim window: how many of the most recent turns are
      kept in full (and shown to the query-rewriter). Every turn that falls out of
      this window is folded into the running summary *incrementally* — one turn per
      update, after every turn — so there is no separate bulk-summarize threshold.
      See ``app/rag/pipeline.py`` (``_update_running_summary``) for the reasoning.
    """

    recent_turns: int = DEFAULT_MEMORY_RECENT_TURNS
    fold_wait_seconds: float = DEFAULT_MEMORY_FOLD_WAIT_SECONDS

    @classmethod
    def from_env(cls) -> "MemorySettings":
        return cls(
            recent_turns=int(os.getenv("MEMORY_RECENT_TURNS") or DEFAULT_MEMORY_RECENT_TURNS),
            fold_wait_seconds=float(
                os.getenv("MEMORY_FOLD_WAIT_SECONDS") or DEFAULT_MEMORY_FOLD_WAIT_SECONDS
            ),
        )


@dataclass(frozen=True)
class ReuseSettings:
    """Retrieval-reuse gate for conversation follow-ups (Phase 8).

    A cheap, deterministic, *non-LLM* check that runs BEFORE retrieval on a
    follow-up turn: it compares the rewritten question's embedding against the
    previous turn's retrieved chunks and, if the best cosine similarity clears
    ``threshold``, reuses those chunks instead of running retrieval again.

    - ``enabled``    whether the reuse check runs at all (needs a conversation).
    - ``threshold``  minimum cosine similarity (in [0, 1]) between the new question
      and the *best* previously-retrieved chunk before we trust the old chunks to
      still cover it. Deliberately well ABOVE the confidence gate (0.35): reuse
      demands strong "same-fact" similarity, not mere answerability.

      Chosen HIGH (0.72) on purpose. Measured on BGE-M3 (CLAUDE.md §4), a genuinely
      new-info follow-up on an adjacent topic still scores high against the old
      chunk (e.g. "how many sick days?" vs the annual-leave chunk ≈ 0.67), and can
      even outscore a legitimate same-chunk follow-up ("...carried over?" ≈ 0.63) —
      so no threshold cleanly separates the two. The costs are asymmetric: a wrong
      reuse skips the chunk that actually answers the question and forces a wrong
      "I don't know", whereas a missed reuse only costs one redundant retrieval. So
      we set the bar above the highest observed new-topic score (~0.67): only
      near-verbatim repeats/clarifications of the *same* fact (~0.75+) reuse; when
      in doubt we retrieve. Like the 0.35 gate this is a *starting point* to be
      validated against logged production similarities, NOT a final value. It never
      bypasses the gate: when reuse fires, the reused chunks still pass through the
      unchanged retrieve→gate→generate path.
    """

    enabled: bool = DEFAULT_RETRIEVAL_REUSE_ENABLED
    threshold: float = DEFAULT_RETRIEVAL_REUSE_THRESHOLD

    @classmethod
    def from_env(cls) -> "ReuseSettings":
        return cls(
            enabled=_env_bool("RETRIEVAL_REUSE_ENABLED", DEFAULT_RETRIEVAL_REUSE_ENABLED),
            threshold=float(
                os.getenv("RETRIEVAL_REUSE_THRESHOLD") or DEFAULT_RETRIEVAL_REUSE_THRESHOLD
            ),
        )


@dataclass(frozen=True)
class RecoverySettings:
    """Bounded retrieval recovery for Retrieval Discovery Gaps.

    The normal retrieve → gate → generate path is unchanged. When the pipeline
    determines available evidence is insufficient (gate miss, or generation finds
    the context insufficient), at most **one** recovery attempt may run: an LLM
    produces alternative retrieval-oriented search expressions (preserving user
    intent), those are retrieved and RRF-fused with the first-pass hits, then the
    unchanged gate + grounded prompt apply again. Recovery never answers the
    question and never weakens grounding. On expander failure the existing path
    continues (graceful degradation).

    - ``enabled``      kill-switch; when false, behaviour matches the pre-recovery pipeline.
    - ``max_queries``  cap on alternate retrieval expressions per recovery attempt.
    """

    enabled: bool = DEFAULT_RECOVERY_ENABLED
    max_queries: int = DEFAULT_RECOVERY_MAX_QUERIES

    @classmethod
    def from_env(cls) -> "RecoverySettings":
        return cls(
            enabled=_env_bool("RECOVERY_ENABLED", DEFAULT_RECOVERY_ENABLED),
            max_queries=int(os.getenv("RECOVERY_MAX_QUERIES") or DEFAULT_RECOVERY_MAX_QUERIES),
        )


@dataclass(frozen=True)
class DecomposeSettings:
    """Compound-question decomposition before retrieval (Phase 18).

    When enabled, a deterministic heuristic detects likely multi-ask questions;
    only then an LLM splits them into standalone sub-questions. Each sub-question
    is retrieved separately; pools are merged and reranked before generation.
    """

    enabled: bool = DEFAULT_DECOMPOSE_ENABLED

    @classmethod
    def from_env(cls) -> "DecomposeSettings":
        return cls(
            enabled=_env_bool("DECOMPOSE_ENABLED", DEFAULT_DECOMPOSE_ENABLED),
        )


@dataclass(frozen=True)
class RequestBudgetSettings:
    """Global per-request deadline for ``RagPipeline.answer()`` (Phase 19)."""

    deadline_seconds: float = DEFAULT_REQUEST_DEADLINE_SECONDS
    min_stage_seconds: float = DEFAULT_BUDGET_MIN_STAGE_SECONDS

    @classmethod
    def from_env(cls) -> "RequestBudgetSettings":
        return cls(
            deadline_seconds=float(
                os.getenv("REQUEST_DEADLINE_SECONDS") or DEFAULT_REQUEST_DEADLINE_SECONDS
            ),
            min_stage_seconds=float(
                os.getenv("REQUEST_BUDGET_MIN_STAGE_SECONDS") or DEFAULT_BUDGET_MIN_STAGE_SECONDS
            ),
        )


@dataclass(frozen=True)
class QueryCacheSettings:
    """Postgres-backed cache for repeated standalone questions (Phase 19)."""

    enabled: bool = DEFAULT_QUERY_CACHE_ENABLED
    ttl_seconds: int = DEFAULT_QUERY_CACHE_TTL_SECONDS

    @classmethod
    def from_env(cls) -> "QueryCacheSettings":
        return cls(
            enabled=_env_bool("QUERY_CACHE_ENABLED", DEFAULT_QUERY_CACHE_ENABLED),
            ttl_seconds=int(os.getenv("QUERY_CACHE_TTL_SECONDS") or DEFAULT_QUERY_CACHE_TTL_SECONDS),
        )


@dataclass(frozen=True)
class RateLimitSettings:
    """Postgres-backed rate limits for HTTP endpoints (Phase 21)."""

    enabled: bool = DEFAULT_RATE_LIMIT_ENABLED
    chat_requests_per_window: int = DEFAULT_RATE_LIMIT_CHAT_REQUESTS
    window_seconds: int = DEFAULT_RATE_LIMIT_WINDOW_SECONDS

    @classmethod
    def from_env(cls) -> "RateLimitSettings":
        return cls(
            enabled=_env_bool("RATE_LIMIT_ENABLED", DEFAULT_RATE_LIMIT_ENABLED),
            chat_requests_per_window=int(
                os.getenv("RATE_LIMIT_CHAT_REQUESTS") or DEFAULT_RATE_LIMIT_CHAT_REQUESTS
            ),
            window_seconds=int(
                os.getenv("RATE_LIMIT_WINDOW_SECONDS") or DEFAULT_RATE_LIMIT_WINDOW_SECONDS
            ),
        )


@dataclass(frozen=True)
class IngestSanitizeSettings:
    """Ingestion-time document size and malformed-content guards (Phase 21)."""

    max_document_chars: int = DEFAULT_INGEST_MAX_DOCUMENT_CHARS
    max_control_char_ratio: float = DEFAULT_INGEST_MAX_CONTROL_CHAR_RATIO

    @classmethod
    def from_env(cls) -> "IngestSanitizeSettings":
        return cls(
            max_document_chars=int(
                os.getenv("INGEST_MAX_DOCUMENT_CHARS") or DEFAULT_INGEST_MAX_DOCUMENT_CHARS
            ),
            max_control_char_ratio=float(
                os.getenv("INGEST_MAX_CONTROL_CHAR_RATIO")
                or DEFAULT_INGEST_MAX_CONTROL_CHAR_RATIO
            ),
        )


@dataclass(frozen=True)
class QueryNormSettings:
    """Corpus-vocab spelling correction before retrieval (Phase 17).

    First-time questions are otherwise embedded raw; only conversational
    follow-ups get an LLM rewrite, and recovery spelling is reactive. This is
    a cheap non-LLM first line — SymSpell against *this org's* chunk vocabulary
    — so typos like "protien" map toward document terms without an LLM call on
    every request. Default max edit distance is 1 (distance 2 falsely "fixed"
    external entity names like Niva→five). See ``app/rag/query_normalize.py``.
    """

    enabled: bool = DEFAULT_QUERY_NORM_ENABLED
    max_edit_distance: int = DEFAULT_QUERY_NORM_MAX_EDIT_DISTANCE
    min_word_length: int = DEFAULT_QUERY_NORM_MIN_WORD_LENGTH

    @classmethod
    def from_env(cls) -> "QueryNormSettings":
        return cls(
            enabled=_env_bool("QUERY_NORM_ENABLED", DEFAULT_QUERY_NORM_ENABLED),
            max_edit_distance=int(
                os.getenv("QUERY_NORM_MAX_EDIT_DISTANCE")
                or DEFAULT_QUERY_NORM_MAX_EDIT_DISTANCE
            ),
            min_word_length=int(
                os.getenv("QUERY_NORM_MIN_WORD_LENGTH")
                or DEFAULT_QUERY_NORM_MIN_WORD_LENGTH
            ),
        )


@dataclass(frozen=True)
class WebSearchSettings:
    """Web-search tool configuration (Phase 5).

    - ``enabled``      whether the RAG pipeline offers the web-search tool at all.
    - ``provider``     ``duckduckgo`` (keyless, default) or ``tavily`` (needs key).
    - ``api_key``      required only by providers that need one (e.g. Tavily).
    - ``max_results``  how many results to fetch and feed back to the model.
    - ``timeout``      hard cap (seconds) on the search call; on timeout the
      pipeline degrades to the fixed internal fallback.
    """

    enabled: bool = DEFAULT_WEB_SEARCH_ENABLED
    provider: str = DEFAULT_WEB_SEARCH_PROVIDER
    api_key: str | None = None
    max_results: int = DEFAULT_WEB_SEARCH_MAX_RESULTS
    timeout: float = DEFAULT_WEB_SEARCH_TIMEOUT

    @classmethod
    def from_env(cls) -> "WebSearchSettings":
        return cls(
            enabled=_env_bool("WEB_SEARCH_ENABLED", DEFAULT_WEB_SEARCH_ENABLED),
            provider=(os.getenv("WEB_SEARCH_PROVIDER") or DEFAULT_WEB_SEARCH_PROVIDER).lower(),
            api_key=os.getenv("WEB_SEARCH_API_KEY"),
            max_results=int(os.getenv("WEB_SEARCH_MAX_RESULTS") or DEFAULT_WEB_SEARCH_MAX_RESULTS),
            timeout=float(os.getenv("WEB_SEARCH_TIMEOUT") or DEFAULT_WEB_SEARCH_TIMEOUT),
        )


@dataclass(frozen=True)
class ContextualSettings:
    """Contextual-retrieval config (Phase 6, ingest-time).

    When enabled, a short LLM-generated context is prepended to each chunk before
    it is embedded and stored, so the chunk carries its surrounding meaning.

    ``concurrency`` is how many of those per-chunk calls run at once within a
    single document. It exists because this was the ingestion bottleneck by a
    wide margin: the calls are independent, network-bound, and were issued
    strictly one after another, so a 15-page workspace could sit at "Syncing…"
    for minutes while the CPU did nothing. Set to 1 to restore the old serial
    behaviour (e.g. against an endpoint that rate-limits aggressively).
    """

    enabled: bool = DEFAULT_CONTEXTUAL_ENABLED
    concurrency: int = DEFAULT_CONTEXTUAL_CONCURRENCY

    @classmethod
    def from_env(cls) -> "ContextualSettings":
        return cls(
            enabled=_env_bool("INGEST_CONTEXTUAL_ENABLED", DEFAULT_CONTEXTUAL_ENABLED),
            concurrency=_env_positive_int(
                "INGEST_CONTEXTUAL_CONCURRENCY", DEFAULT_CONTEXTUAL_CONCURRENCY
            ),
        )


@dataclass(frozen=True)
class RetrievalSettings:
    """Query-time retrieval config (Phase 6).

    - ``hybrid_enabled``   fuse keyword (BM25-style) results with vector results.
    - ``rerank_enabled``   cross-encoder rerank the fused candidate pool.
    - ``candidate_pool``   how many candidates to fetch (per signal) and rerank
      before selecting the final ``RagSettings.top_k``.
    - ``rrf_k``            Reciprocal Rank Fusion constant.
    """

    hybrid_enabled: bool = DEFAULT_RETRIEVAL_HYBRID_ENABLED
    rerank_enabled: bool = DEFAULT_RETRIEVAL_RERANK_ENABLED
    candidate_pool: int = DEFAULT_RETRIEVAL_CANDIDATE_POOL
    rrf_k: int = DEFAULT_RETRIEVAL_RRF_K

    @classmethod
    def from_env(cls) -> "RetrievalSettings":
        return cls(
            hybrid_enabled=_env_bool("RETRIEVAL_HYBRID_ENABLED", DEFAULT_RETRIEVAL_HYBRID_ENABLED),
            rerank_enabled=_env_bool("RETRIEVAL_RERANK_ENABLED", DEFAULT_RETRIEVAL_RERANK_ENABLED),
            candidate_pool=int(
                os.getenv("RETRIEVAL_CANDIDATE_POOL") or DEFAULT_RETRIEVAL_CANDIDATE_POOL
            ),
            rrf_k=int(os.getenv("RETRIEVAL_RRF_K") or DEFAULT_RETRIEVAL_RRF_K),
        )


@dataclass(frozen=True)
class RerankerSettings:
    """Cross-encoder reranker config (Phase 6)."""

    model: str = DEFAULT_RERANKER_MODEL
    device: str | None = None

    @classmethod
    def from_env(cls) -> "RerankerSettings":
        return cls(
            model=os.getenv("RERANKER_MODEL") or DEFAULT_RERANKER_MODEL,
            device=os.getenv("RERANKER_DEVICE") or None,
        )


@dataclass(frozen=True)
class AuthSettings:
    """Auth/session/credential-encryption configuration (Phase 10).

    - ``encryption_keys``  Fernet key(s) used to encrypt/decrypt stored OAuth
      tokens (see ``app/security/crypto.py``). Read as a comma-separated list so
      a key can be rotated by prepending a new one — the first key is used to
      encrypt new values, every key is tried on decrypt. Each entry must be a
      valid ``Fernet.generate_key()`` value.
    - ``jwt_secret``  signs the session cookie issued after magic-link/OAuth
      login. Required in any real deployment; no default (must not silently run
      with a well-known key).
    - ``session_ttl_minutes``  session cookie lifetime. Defaults to 30 days
      (long-lived, not a short web session) — deliberate given this is a
      low-risk internal tool with an already-hardened cookie
      (httpOnly+Secure+SameSite=Lax) and no refresh-token mechanism.
    - ``magic_link_ttl_minutes``  how long a login link stays valid/single-use.
    - ``signup_action_ttl_hours``  how long the one-click approve/reject links
      emailed to the platform owner on a new ``/auth/signup`` request stay
      valid/single-use (``org_signup_requests.action_expires_at``).

    Self-serve org creation (``/auth/signup``) is gated by human review, not a
    pre-approved list — see the signup-approval queue
    (``app/auth/signup_requests.py``, reviewed via the one-click email links
    in ``app/api/auth.py``). This superseded an earlier DB-backed
    ``owner_email_whitelist`` design.
    """

    encryption_keys: list[str] = field(default_factory=list)
    jwt_secret: str | None = None
    session_ttl_minutes: int = DEFAULT_SESSION_TTL_MINUTES
    magic_link_ttl_minutes: int = DEFAULT_MAGIC_LINK_TTL_MINUTES
    signup_action_ttl_hours: int = DEFAULT_SIGNUP_ACTION_TTL_HOURS

    @classmethod
    def from_env(cls) -> "AuthSettings":
        raw_keys = os.getenv("AUTH_ENCRYPTION_KEYS") or ""
        keys = [key.strip() for key in raw_keys.split(",") if key.strip()]
        return cls(
            encryption_keys=keys,
            jwt_secret=os.getenv("AUTH_JWT_SECRET"),
            session_ttl_minutes=int(
                os.getenv("AUTH_SESSION_TTL_MINUTES") or DEFAULT_SESSION_TTL_MINUTES
            ),
            magic_link_ttl_minutes=int(
                os.getenv("AUTH_MAGIC_LINK_TTL_MINUTES") or DEFAULT_MAGIC_LINK_TTL_MINUTES
            ),
            signup_action_ttl_hours=int(
                os.getenv("AUTH_SIGNUP_ACTION_TTL_HOURS") or DEFAULT_SIGNUP_ACTION_TTL_HOURS
            ),
        )


@dataclass(frozen=True)
class ApiSettings:
    """HTTP API server configuration (Phase 10+).

    - ``cors_origins``  exact frontend origin(s) allowed to call the API with
      credentials (cookies). No wildcard default — an empty list means CORS is
      not configured yet, which the API layer should treat as "reject", not
      "allow all".
    - ``frontend_url``  base URL of the deployed frontend, used to build the
      magic-link login URL and the post-OAuth-connect redirect destination.
    """

    cors_origins: list[str] = field(default_factory=list)
    host: str = DEFAULT_API_HOST
    port: int = DEFAULT_API_PORT
    frontend_url: str | None = None

    @classmethod
    def from_env(cls) -> "ApiSettings":
        raw_origins = os.getenv("API_CORS_ORIGINS") or ""
        origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
        return cls(
            cors_origins=origins,
            host=os.getenv("API_HOST") or DEFAULT_API_HOST,
            port=int(os.getenv("API_PORT") or DEFAULT_API_PORT),
            frontend_url=os.getenv("FRONTEND_URL"),
        )


@dataclass(frozen=True)
class EmailSettings:
    """Outbound email configuration for magic-link delivery (Phase 10).

    - ``sender``  ``"console"`` (default; prints the link, no dependency — dev
      and self-hosted-without-SMTP path) or ``"smtp"``.
    - ``smtp_*``  only read/required when ``sender == "smtp"``.
    - ``owner_notification_email``  where the "new org-signup request" email
      (with one-click approve/reject links) is sent. This is the ONLY review
      surface for the signup-approval queue — there is no admin UI or CLI, so
      leaving this unset means pending requests are only visible via a direct
      query against ``org_signup_requests``.
    """

    sender: str = DEFAULT_EMAIL_SENDER
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    owner_notification_email: str | None = None

    @classmethod
    def from_env(cls) -> "EmailSettings":
        smtp_port = os.getenv("EMAIL_SMTP_PORT")
        return cls(
            sender=(os.getenv("EMAIL_SENDER") or DEFAULT_EMAIL_SENDER).lower(),
            smtp_host=os.getenv("EMAIL_SMTP_HOST"),
            smtp_port=int(smtp_port) if smtp_port else None,
            smtp_username=os.getenv("EMAIL_SMTP_USERNAME"),
            smtp_password=os.getenv("EMAIL_SMTP_PASSWORD"),
            smtp_from=os.getenv("EMAIL_SMTP_FROM"),
            owner_notification_email=os.getenv("OWNER_NOTIFICATION_EMAIL"),
        )
