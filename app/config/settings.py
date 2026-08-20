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
# "heuristic" (default) estimates token counts with zero dependencies and ~0MB.
# "hf" loads the exact BGE-M3 tokenizer, which measures at ~378MB RSS and so
# CANNOT fit alongside the API in a 512MB box — opt in only where memory allows
# (see app/ingestion/chunk_tokens.py for the measurements).
DEFAULT_CHUNK_TOKEN_BACKEND = "heuristic"
# Absolute per-chunk character ceiling — a backstop on the TOKEN budget above,
# not a second way to express it. Token counts (exact or estimated) can be
# arbitrarily wrong on text no tokenizer segments the way prose is segmented:
# measured against the real BGE-M3 tokenizer, a run of base64 bills 1 token per
# CHARACTER, so the heuristic under-counts it 16x. Since 1 token/char is the
# worst case any input can reach, bounding characters bounds tokens outright —
# 4000 chars can never exceed the embedding model's 8192-token limit, whatever
# the content. A legitimate 256-token prose chunk is ~1000-1300 chars, so this
# never fires on real documents (the golden corpus tops out at 505).
DEFAULT_MAX_CHUNK_CHARS = 4000

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

# The Slack agent's refusal. Distinct from the policy/workspace copy because
# the *next step* differs: a Slack miss usually means the conversation happened
# in a channel that isn't connected, or outside the backfill window — both of
# which the user can act on, unlike "it isn't in the handbook".
DEFAULT_SLACK_FALLBACK_RESPONSE = (
    "I couldn't find that in the connected Slack channels. It may have been "
    "discussed in a channel that isn't connected, or before the synced history "
    "window."
)

# The Linear agent's refusal. Distinct copy for the same reason as Slack's: the
# actionable next step differs from "it isn't in the handbook" — the issue may
# just not be ingested yet, or the question doesn't match any tracked issue.
DEFAULT_LINEAR_FALLBACK_RESPONSE = (
    "I couldn't find that in the connected Linear issues. It may not have been "
    "ingested yet, or no issue matches this question."
)

# The Notion/Drive agents' refusals. Distinct per source (not shared with the
# old combined "policy" copy) so a miss on the Notion tab doesn't imply "check
# your Drive docs" and vice versa — each tab can only ever point at its own
# connected source.
DEFAULT_NOTION_FALLBACK_RESPONSE = (
    "I couldn't find that in the connected Notion pages. It may not have been "
    "shared with the integration, or hasn't been ingested yet."
)
DEFAULT_DRIVE_FALLBACK_RESPONSE = (
    "I couldn't find that in the connected Google Drive documents. It may not "
    "be in the synced folder, or hasn't been ingested yet."
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
DEFAULT_CONTEXTUAL_ENABLED = True          # keep the Phase 6 quality path
DEFAULT_CONTEXTUAL_DEFER = True            # run it AFTER sync succeeds (no onboarding stall)
DEFAULT_CONTEXTUAL_CONCURRENCY = 2         # background enrich; keep low vs 15 RPM free endpoints
DEFAULT_CONTEXTUAL_MAX_CHUNKS = 200        # skip enrich (raw chunks stay) above this many chunks/doc
DEFAULT_HYPOTHETICAL_QUESTIONS_ENABLED = False  # off: changes stored chunk content, opt-in
DEFAULT_EMBED_BATCH_SIZE = 16              # encode in batches (avoids OOM on large docs)
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
DEFAULT_TONE_CLASSIFY_ENABLED = True
DEFAULT_RECOVERY_ENABLED = True
DEFAULT_RECOVERY_MAX_QUERIES = 2

DEFAULT_AUDIT_ENABLED = False

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
# Unauthenticated magic-link requests per IP per window. Higher than chat
# because a whole office behind one NAT shares this bucket, but far below
# what bulk account enumeration needs.
DEFAULT_RATE_LIMIT_AUTH_REQUESTS = 60
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60

# Ingestion sanitization (Phase 21).
DEFAULT_INGEST_MAX_DOCUMENT_CHARS = 2_000_000
DEFAULT_INGEST_MAX_CONTROL_CHAR_RATIO = 0.05

# Ingestion worker memory admission gate (defense-in-depth alongside the
# per-input bounds above): before claiming a new job, the worker checks its
# own current RSS and skips claiming — leaving the job queued for the next
# tick — if already close to this ceiling. This catches memory pressure from
# ANY cause, not just the specific unbounded inputs found and fixed by hand
# (Notion fetch size, remote-embed batching, contextual chunk-count cap).
# Default leaves ~112MB headroom under Render free's hard 512MB ceiling.
DEFAULT_INGEST_MEMORY_GUARD_ENABLED = True
DEFAULT_INGEST_MAX_RSS_MB = 400.0

# Lightweight query spelling/normalization (Phase 17). Corpus-vocab
# SymSpell — no LLM on the happy path. Kill-switch: QUERY_NORM_ENABLED=false.
DEFAULT_QUERY_NORM_ENABLED = True
DEFAULT_QUERY_NORM_MAX_EDIT_DISTANCE = 1
DEFAULT_QUERY_NORM_MIN_WORD_LENGTH = 4
# The per-org SymSpell dictionary is cached for the life of the process with
# nothing evicting it (CLAUDE.md's query-latency section flagged this as a
# known, never-fixed slow-leak risk once more than a handful of orgs are
# onboarded). Bounding it to the N most-recently-used orgs turns unbounded
# growth into a fixed ceiling with no behavior change for a single-org or
# small-org deployment.
DEFAULT_QUERY_NORM_CACHE_MAX_ORGS = 50

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


def env_bool(name: str, default: bool) -> bool:
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
    - ``batch_size``  send/encode at most this many texts per call, on EITHER
      backend, so a document with an unusually large number of chunks cannot
      build one unbounded request (local: OOM the in-process model; remote:
      one huge HTTP request/response payload) in a single shot.
    """

    backend: str
    model: str | None
    device: str | None
    api_key: str | None
    base_url: str | None
    timeout: float = DEFAULT_TIMEOUT
    batch_size: int = DEFAULT_EMBED_BATCH_SIZE

    @classmethod
    def from_env(cls) -> "EmbeddingSettings":
        return cls(
            backend=(os.getenv("EMBEDDING_BACKEND") or DEFAULT_EMBEDDING_BACKEND).lower(),
            model=os.getenv("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL,
            device=os.getenv("EMBEDDING_DEVICE") or None,
            api_key=os.getenv("EMBEDDING_API_KEY"),
            base_url=os.getenv("EMBEDDING_BASE_URL"),
            timeout=float(os.getenv("EMBEDDING_TIMEOUT") or DEFAULT_TIMEOUT),
            batch_size=max(
                1, int(os.getenv("EMBED_BATCH_SIZE") or DEFAULT_EMBED_BATCH_SIZE)
            ),
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
    token_backend: str = DEFAULT_CHUNK_TOKEN_BACKEND
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS

    @classmethod
    def from_env(cls) -> "ChunkingSettings":
        return cls(
            chunk_size=int(os.getenv("CHUNK_SIZE") or DEFAULT_CHUNK_SIZE),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP") or DEFAULT_CHUNK_OVERLAP),
            max_chunk_chars=int(
                os.getenv("CHUNK_MAX_CHARS") or DEFAULT_MAX_CHUNK_CHARS
            ),
            token_backend=(
                os.getenv("CHUNK_TOKEN_BACKEND") or DEFAULT_CHUNK_TOKEN_BACKEND
            ).strip().lower(),
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
class SlackAgentSettings:
    """Configuration specific to ``SlackAgent`` — same shape as
    ``WorkspaceAgentSettings``: a second, independent ``RagPipeline`` instance
    differing only in prompt framing and its own fixed fallback string (which
    must be exact-string-matched consistently *within* that one pipeline by the
    gate, the prompt, and refusal detection).
    """

    fallback_response: str = DEFAULT_SLACK_FALLBACK_RESPONSE

    @classmethod
    def from_env(cls) -> "SlackAgentSettings":
        return cls(
            fallback_response=os.getenv("SLACK_FALLBACK_RESPONSE")
            or DEFAULT_SLACK_FALLBACK_RESPONSE,
        )


@dataclass(frozen=True)
class LinearAgentSettings:
    """Configuration specific to ``LinearAgent`` — same shape as
    ``SlackAgentSettings``: a second, independent ``RagPipeline`` instance
    differing only in prompt framing and its own fixed fallback string.
    """

    fallback_response: str = DEFAULT_LINEAR_FALLBACK_RESPONSE

    @classmethod
    def from_env(cls) -> "LinearAgentSettings":
        return cls(
            fallback_response=os.getenv("LINEAR_FALLBACK_RESPONSE")
            or DEFAULT_LINEAR_FALLBACK_RESPONSE,
        )


@dataclass(frozen=True)
class NotionAgentSettings:
    """Configuration specific to ``NotionAgent`` — same shape as
    ``SlackAgentSettings``/``LinearAgentSettings``: a second, independent
    ``RagPipeline`` instance differing only in prompt framing and its own
    fixed fallback string.
    """

    fallback_response: str = DEFAULT_NOTION_FALLBACK_RESPONSE

    @classmethod
    def from_env(cls) -> "NotionAgentSettings":
        return cls(
            fallback_response=os.getenv("NOTION_AGENT_FALLBACK_RESPONSE")
            or DEFAULT_NOTION_FALLBACK_RESPONSE,
        )


@dataclass(frozen=True)
class DriveAgentSettings:
    """Configuration specific to ``DriveAgent`` — same shape as ``NotionAgentSettings``."""

    fallback_response: str = DEFAULT_DRIVE_FALLBACK_RESPONSE

    @classmethod
    def from_env(cls) -> "DriveAgentSettings":
        return cls(
            fallback_response=os.getenv("DRIVE_AGENT_FALLBACK_RESPONSE")
            or DEFAULT_DRIVE_FALLBACK_RESPONSE,
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


# "read" is Linear's minimal read-only OAuth scope — enough for the GraphQL
# reads LinearAdapter issues (issues + comments), never write/admin.
DEFAULT_LINEAR_OAUTH_SCOPES = "read"


@dataclass(frozen=True)
class LinearSettings:
    """Configuration for the Linear content source.

    Two independent, non-fallback-linked credential paths — same coexistence
    as ``NotionSettings`` (legacy static token vs. OAuth):

    - ``token``/``tokens``: a personal API key is the simplest viable auth (no
      OAuth app review needed), and per-org keys are discovered generically
      from ``LINEAR_TOKEN_<NAME>`` env vars so a key can only see the Linear
      workspace it belongs to — the tenant boundary is enforced by Linear
      itself, not just our code. Used by ``scripts/ingest_linear.py`` /
      ``LinearAdapter`` when built with ``token_name``/no ``token``.
    - ``client_id``/``client_secret``/``redirect_uri``/``scopes``: the OAuth
      "Connect Linear" flow (``app/auth/linear_oauth.py``), for the admin
      self-serve path the product's other sources use. A token obtained this
      way is passed to ``LinearAdapter`` as ``token=`` (already-resolved),
      exactly like Google/Slack's OAuth-only paths — see
      ``app/sources/factory.py``.
    """

    token: str | None
    tokens: dict[str, str] = field(default_factory=dict)
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    scopes: str = DEFAULT_LINEAR_OAUTH_SCOPES

    _TOKEN_PREFIX = "LINEAR_TOKEN_"

    @classmethod
    def from_env(cls) -> "LinearSettings":
        tokens = {
            key[len(cls._TOKEN_PREFIX):].lower(): value
            for key, value in os.environ.items()
            if key.startswith(cls._TOKEN_PREFIX)
            and len(key) > len(cls._TOKEN_PREFIX)
            and value
        }
        return cls(
            token=os.getenv("LINEAR_TOKEN"),
            tokens=tokens,
            client_id=os.getenv("LINEAR_CLIENT_ID"),
            client_secret=os.getenv("LINEAR_CLIENT_SECRET"),
            redirect_uri=os.getenv("LINEAR_REDIRECT_URI"),
            scopes=os.getenv("LINEAR_OAUTH_SCOPES", DEFAULT_LINEAR_OAUTH_SCOPES),
        )

    def resolve_token(self, name: str | None = None) -> str:
        """Return the API key for ``name`` (or the default token). No fallback."""
        from ..core.exceptions import ConfigurationError

        if name:
            token = self.tokens.get(name.lower())
            if not token:
                available = ", ".join(sorted(self.tokens)) or "(none configured)"
                raise ConfigurationError(
                    f"No Linear token named {name!r}. Set LINEAR_TOKEN_{name.upper()} "
                    f"in your .env. Configured org tokens: {available}."
                )
            return token
        if self.token:
            return self.token
        raise ConfigurationError(
            "No Linear token configured. Set LINEAR_TOKEN (default) or a per-org "
            "LINEAR_TOKEN_<NAME> and pass its name to the ingestion run."
        )


DEFAULT_GOOGLE_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/drive.readonly "
    "https://www.googleapis.com/auth/documents.readonly"
)
# Ceilings on the Drive folder crawl (see GoogleSettings). 500 folders is far
# more than a policy folder needs while still bounding the number of sequential
# Google API calls a single request can issue; 2000 native Docs likewise. Both
# are truncation points that get LOGGED, never silent — a sync that quietly
# indexed half a folder is worse than one that says it stopped.
DEFAULT_GOOGLE_MAX_WALK_FOLDERS = 500
DEFAULT_GOOGLE_MAX_DOCUMENTS = 2000


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
    # Bounds on the recursive folder walk. Drive's `parents` field is
    # direct-parent-only, so there is no server-side "all descendants" query and
    # the adapter must crawl: one files.list call PER folder. Depth was already
    # capped, but breadth was not — a wide tree meant an unbounded number of
    # sequential Google calls inside one request, and this same walk runs on the
    # Sources page's change-check. Same lesson as the Notion fetch bound: cap the
    # walk itself, don't just cap what it produces.
    max_walk_folders: int = DEFAULT_GOOGLE_MAX_WALK_FOLDERS
    max_documents: int = DEFAULT_GOOGLE_MAX_DOCUMENTS

    @classmethod
    def from_env(cls) -> "GoogleSettings":
        return cls(
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            redirect_uri=os.getenv("GOOGLE_REDIRECT_URI"),
            scopes=os.getenv("GOOGLE_OAUTH_SCOPES", DEFAULT_GOOGLE_OAUTH_SCOPES),
            max_walk_folders=int(
                os.getenv("GOOGLE_MAX_WALK_FOLDERS") or DEFAULT_GOOGLE_MAX_WALK_FOLDERS
            ),
            max_documents=int(
                os.getenv("GOOGLE_MAX_DOCUMENTS") or DEFAULT_GOOGLE_MAX_DOCUMENTS
            ),
        )


# Phase 1 of Slack Integration Plan (docs/plans/2026-08-17-slack-integration.md):
# bot scopes only — enough to list/join/read channels and resolve display
# names. No `chat:write` (read-only connector, D-note in the plan's non-goals).
# `users:read.email` (workspace-invite member picker) is the one exception to
# "read-only" in spirit, not in effect — it only ever resolves a Slack
# member's email so it can be matched against an EXISTING org account; it
# never lets Handbook message or modify anything in Slack. Existing
# connections must reconnect once to pick up a newly added scope (Slack
# re-consents on scope change, it doesn't apply retroactively).
DEFAULT_SLACK_BOT_SCOPES = (
    "channels:history,channels:read,channels:join,"
    "groups:history,groups:read,"
    "im:history,im:read,mpim:history,mpim:read,"
    "users:read,users:read.email"
)

# Phase 2 bounds (plan §6/D11 — "bound the walk itself" discipline, same as
# GOOGLE_MAX_WALK_FOLDERS/GOOGLE_MAX_DOCUMENTS above and the Notion fetch-size
# bound in CLAUDE.md §4). A busy channel's full history is not safe to embed
# unbounded, so every knob here caps something rather than trusting input size.
DEFAULT_SLACK_BACKFILL_DAYS = 90
DEFAULT_SLACK_MIN_THREAD_CHARS = 40
DEFAULT_SLACK_MAX_THREAD_MESSAGES = 50
DEFAULT_SLACK_MAX_DOCUMENTS_PER_SYNC = 20000


@dataclass(frozen=True)
class SlackSettings:
    """Configuration for Slack: OAuth "Connect" flow + adapter ingest bounds.

    Same shape as ``GoogleSettings`` — OAuth-only, no legacy static-token
    path, with adapter walk-bounds living in the same dataclass rather than a
    second one (mirrors ``max_walk_folders``/``max_documents`` living on
    ``GoogleSettings`` itself). ``scopes`` is comma-delimited (Slack's own
    convention for the ``scope`` request parameter on ``oauth/v2/authorize``).
    """

    client_id: str | None
    client_secret: str | None
    redirect_uri: str | None
    scopes: str = DEFAULT_SLACK_BOT_SCOPES
    # First-sync backfill window. Slack's `conversations.history` sits in one
    # of Slack's more restricted rate tiers and has been tightened further for
    # non-Marketplace apps pulling non-recent history, so a channel's full
    # history is never pulled in one run — only the last N days.
    backfill_days: int = DEFAULT_SLACK_BACKFILL_DAYS
    # A standalone (no-reply) message shorter than this is skipped before it
    # ever becomes a document — most single-line Slack chatter ("thanks",
    # ":+1:") is noise that would only compete with a real answer in
    # retrieval. Threads WITH replies are never filtered this way (a real
    # conversation happened), only lone short messages.
    min_thread_chars: int = DEFAULT_SLACK_MIN_THREAD_CHARS
    # A thread with more replies than this is rendered from only the most
    # recent N, with a truncation marker — same per-unit size bound as
    # CHUNK_MAX_CHARS, applied at the thread level instead of the chunk level.
    max_thread_messages: int = DEFAULT_SLACK_MAX_THREAD_MESSAGES
    # Aggregate cap across one list_documents() call (summed over every
    # configured channel) — bounds the SUM, which backfill_days/min_thread_chars/
    # max_thread_messages each bound only per-channel/per-thread, not in total.
    max_documents_per_sync: int = DEFAULT_SLACK_MAX_DOCUMENTS_PER_SYNC

    @classmethod
    def from_env(cls) -> "SlackSettings":
        return cls(
            client_id=os.getenv("SLACK_CLIENT_ID"),
            client_secret=os.getenv("SLACK_CLIENT_SECRET"),
            redirect_uri=os.getenv("SLACK_REDIRECT_URI"),
            scopes=os.getenv("SLACK_BOT_SCOPES", DEFAULT_SLACK_BOT_SCOPES),
            backfill_days=int(
                os.getenv("SLACK_BACKFILL_DAYS") or DEFAULT_SLACK_BACKFILL_DAYS
            ),
            min_thread_chars=int(
                os.getenv("SLACK_MIN_THREAD_CHARS") or DEFAULT_SLACK_MIN_THREAD_CHARS
            ),
            max_thread_messages=int(
                os.getenv("SLACK_MAX_THREAD_MESSAGES") or DEFAULT_SLACK_MAX_THREAD_MESSAGES
            ),
            max_documents_per_sync=int(
                os.getenv("SLACK_MAX_DOCUMENTS_PER_SYNC") or DEFAULT_SLACK_MAX_DOCUMENTS_PER_SYNC
            ),
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
            enabled=env_bool("RETRIEVAL_REUSE_ENABLED", DEFAULT_RETRIEVAL_REUSE_ENABLED),
            threshold=float(
                os.getenv("RETRIEVAL_REUSE_THRESHOLD") or DEFAULT_RETRIEVAL_REUSE_THRESHOLD
            ),
        )



@dataclass(frozen=True)
class ToneSettings:
    """Semantic question-tone classification (factual vs supportive).

    A cheap aux-LLM call labels whether the user is asking for policy
    information or personally seeking help coping. Empathy is required only
    for ``supportive``; policy-topic asks (even mental health) stay factual.
    Kill-switch: ``TONE_CLASSIFY_ENABLED=false`` falls back to prompt-only
    judgement with no empathy force/strip retry.
    """

    enabled: bool = DEFAULT_TONE_CLASSIFY_ENABLED

    @classmethod
    def from_env(cls) -> "ToneSettings":
        return cls(
            enabled=env_bool("TONE_CLASSIFY_ENABLED", DEFAULT_TONE_CLASSIFY_ENABLED),
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
            enabled=env_bool("RECOVERY_ENABLED", DEFAULT_RECOVERY_ENABLED),
            max_queries=int(os.getenv("RECOVERY_MAX_QUERIES") or DEFAULT_RECOVERY_MAX_QUERIES),
        )


@dataclass(frozen=True)
class AuditSettings:
    """Post-generation groundedness audit — the validation-layer gap (CLAUDE.md
    §6 Phase 20, previously deferred pending a latency/cost decision).

    A bounded second-opinion LLM call re-checks an already-drafted Mode A/B
    answer against the same retrieved context. It can only downgrade an
    answer to the fixed fallback, never edit or extend one, and any audit
    failure (LLM error, unparseable verdict) degrades to skipping the audit
    — the original answer stands. Default OFF: this is additive cost on top
    of every answered question, so it opts in rather than changing existing
    behaviour/latency by default.

    - ``enabled``  kill-switch; off means byte-identical behaviour to before
      this existed.
    """

    enabled: bool = DEFAULT_AUDIT_ENABLED

    @classmethod
    def from_env(cls) -> "AuditSettings":
        return cls(enabled=env_bool("RAG_AUDIT_ENABLED", DEFAULT_AUDIT_ENABLED))


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
            enabled=env_bool("DECOMPOSE_ENABLED", DEFAULT_DECOMPOSE_ENABLED),
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
            enabled=env_bool("QUERY_CACHE_ENABLED", DEFAULT_QUERY_CACHE_ENABLED),
            ttl_seconds=int(os.getenv("QUERY_CACHE_TTL_SECONDS") or DEFAULT_QUERY_CACHE_TTL_SECONDS),
        )


@dataclass(frozen=True)
class RateLimitSettings:
    """Postgres-backed rate limits for HTTP endpoints (Phase 21)."""

    enabled: bool = DEFAULT_RATE_LIMIT_ENABLED
    chat_requests_per_window: int = DEFAULT_RATE_LIMIT_CHAT_REQUESTS
    # Separate budget for the unauthenticated magic-link endpoint. It must NOT
    # share the chat limit: chat is per-org and per-session, this is per-IP with
    # no session at all, so a whole office behind one NAT shares a single
    # bucket. Sized to bound bulk account enumeration (see app/api/auth.py)
    # while leaving room for everyone in a company to sign in at once.
    auth_requests_per_window: int = DEFAULT_RATE_LIMIT_AUTH_REQUESTS
    window_seconds: int = DEFAULT_RATE_LIMIT_WINDOW_SECONDS

    @classmethod
    def from_env(cls) -> "RateLimitSettings":
        return cls(
            enabled=env_bool("RATE_LIMIT_ENABLED", DEFAULT_RATE_LIMIT_ENABLED),
            chat_requests_per_window=int(
                os.getenv("RATE_LIMIT_CHAT_REQUESTS") or DEFAULT_RATE_LIMIT_CHAT_REQUESTS
            ),
            auth_requests_per_window=int(
                os.getenv("RATE_LIMIT_AUTH_REQUESTS") or DEFAULT_RATE_LIMIT_AUTH_REQUESTS
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
class IngestWorkerSettings:
    """Ingestion worker memory admission gate (see ``DEFAULT_INGEST_MAX_RSS_MB``
    above for the full reasoning). Checked once per ``run_once()`` call, before
    ``queue.claim_next()`` — a coarse, cheap proactive circuit breaker, not a
    replacement for the per-input bounds elsewhere in the ingestion pipeline.
    """

    memory_guard_enabled: bool = DEFAULT_INGEST_MEMORY_GUARD_ENABLED
    max_rss_mb: float = DEFAULT_INGEST_MAX_RSS_MB

    @classmethod
    def from_env(cls) -> "IngestWorkerSettings":
        return cls(
            memory_guard_enabled=env_bool(
                "INGEST_MEMORY_GUARD_ENABLED", DEFAULT_INGEST_MEMORY_GUARD_ENABLED
            ),
            max_rss_mb=float(os.getenv("INGEST_MAX_RSS_MB") or DEFAULT_INGEST_MAX_RSS_MB),
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
    cache_max_orgs: int = DEFAULT_QUERY_NORM_CACHE_MAX_ORGS

    @classmethod
    def from_env(cls) -> "QueryNormSettings":
        return cls(
            enabled=env_bool("QUERY_NORM_ENABLED", DEFAULT_QUERY_NORM_ENABLED),
            max_edit_distance=int(
                os.getenv("QUERY_NORM_MAX_EDIT_DISTANCE")
                or DEFAULT_QUERY_NORM_MAX_EDIT_DISTANCE
            ),
            min_word_length=int(
                os.getenv("QUERY_NORM_MIN_WORD_LENGTH")
                or DEFAULT_QUERY_NORM_MIN_WORD_LENGTH
            ),
            cache_max_orgs=_env_positive_int(
                "QUERY_NORM_CACHE_MAX_ORGS", DEFAULT_QUERY_NORM_CACHE_MAX_ORGS
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
            enabled=env_bool("WEB_SEARCH_ENABLED", DEFAULT_WEB_SEARCH_ENABLED),
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

    ``defer`` (default **on**): sync first embeds raw chunks and marks the job
    succeeded so onboarding/chat unlock immediately; contextualize + re-embed
    then runs in the worker as a best-effort ``enriching`` phase. That keeps the
    Phase 6 quality intent without free-tier Gemini stalling users at "0 of N".
    Set ``INGEST_CONTEXTUAL_DEFER=false`` for the old inline (blocking) behaviour.

    ``concurrency`` is how many per-chunk calls run at once within one document
    during inline or deferred enrich. Keep low (1–2) on free/metered endpoints.

    ``max_chunks``: a document that chunks into more than this many pieces
    skips contextual enrichment entirely (it keeps its plain, already-embedded
    chunks — the same safe state every chunk starts in under ``defer``) rather
    than issuing one sequential/low-concurrency LLM call per chunk with no
    upper bound. A pathologically large single document (an entire book pasted
    into one page) would otherwise tie up the worker for a very long time; this
    makes that a bounded, cheap no-op instead, without touching normal-sized
    documents (a typical page is well under this).
    """

    enabled: bool = DEFAULT_CONTEXTUAL_ENABLED
    defer: bool = DEFAULT_CONTEXTUAL_DEFER
    concurrency: int = DEFAULT_CONTEXTUAL_CONCURRENCY
    max_chunks: int = DEFAULT_CONTEXTUAL_MAX_CHUNKS
    # Metadata-creation gap (production-RAG comparison #2): fold 2-3 LLM-
    # generated hypothetical questions into the SAME contextualize call
    # (never a second LLM round-trip per chunk — this endpoint is already
    # rate-limited, see the 15rpm gotcha elsewhere in this file) and append
    # them to the stored chunk text, so a rephrased user question can match
    # one of them via vector OR keyword search. Independent kill-switch from
    # ``enabled`` because it changes stored chunk *content*, not just
    # whether contextualizing runs at all — default OFF so no existing
    # ingest/eval output shape changes unless explicitly turned on.
    hypothetical_questions: bool = DEFAULT_HYPOTHETICAL_QUESTIONS_ENABLED

    @classmethod
    def from_env(cls) -> "ContextualSettings":
        return cls(
            enabled=env_bool("INGEST_CONTEXTUAL_ENABLED", DEFAULT_CONTEXTUAL_ENABLED),
            defer=env_bool("INGEST_CONTEXTUAL_DEFER", DEFAULT_CONTEXTUAL_DEFER),
            concurrency=_env_positive_int(
                "INGEST_CONTEXTUAL_CONCURRENCY", DEFAULT_CONTEXTUAL_CONCURRENCY
            ),
            max_chunks=_env_positive_int(
                "INGEST_CONTEXTUAL_MAX_CHUNKS", DEFAULT_CONTEXTUAL_MAX_CHUNKS
            ),
            hypothetical_questions=env_bool(
                "INGEST_HYPOTHETICAL_QUESTIONS_ENABLED",
                DEFAULT_HYPOTHETICAL_QUESTIONS_ENABLED,
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
            hybrid_enabled=env_bool("RETRIEVAL_HYBRID_ENABLED", DEFAULT_RETRIEVAL_HYBRID_ENABLED),
            rerank_enabled=env_bool("RETRIEVAL_RERANK_ENABLED", DEFAULT_RETRIEVAL_RERANK_ENABLED),
            candidate_pool=int(
                os.getenv("RETRIEVAL_CANDIDATE_POOL") or DEFAULT_RETRIEVAL_CANDIDATE_POOL
            ),
            rrf_k=int(os.getenv("RETRIEVAL_RRF_K") or DEFAULT_RETRIEVAL_RRF_K),
        )


@dataclass(frozen=True)
class RerankerSettings:
    """Cross-encoder / remote reranker config (Phase 6 + Jina remote).

    - ``backend``  ``local`` (in-process CrossEncoder) or ``remote`` (HTTP
      Jina-compatible ``/v1/rerank``). Remote is for cloud deploys that cannot
      hold ``bge-reranker-v2-m3`` in RAM.
    - ``api_key`` / ``base_url``  remote only. Key falls back to
      ``EMBEDDING_API_KEY`` so one Jina key covers embed + rerank; base URL
      falls back to ``EMBEDDING_BASE_URL`` then Jina's public endpoint.
    """

    backend: str = "local"
    model: str = DEFAULT_RERANKER_MODEL
    device: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    timeout: float = DEFAULT_TIMEOUT

    @classmethod
    def from_env(cls) -> "RerankerSettings":
        backend = (os.getenv("RERANKER_BACKEND") or "local").lower()
        default_model = (
            "jina-reranker-v3" if backend == "remote" else DEFAULT_RERANKER_MODEL
        )
        api_key = os.getenv("RERANKER_API_KEY") or os.getenv("EMBEDDING_API_KEY")
        base_url = (
            os.getenv("RERANKER_BASE_URL")
            or os.getenv("EMBEDDING_BASE_URL")
            or ("https://api.jina.ai/v1" if backend == "remote" else None)
        )
        return cls(
            backend=backend,
            model=os.getenv("RERANKER_MODEL") or default_model,
            device=os.getenv("RERANKER_DEVICE") or None,
            api_key=api_key,
            base_url=base_url,
            timeout=float(os.getenv("RERANKER_TIMEOUT") or DEFAULT_TIMEOUT),
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

    - ``sender``  ``"console"`` (default; prints the link), ``"smtp"``,
      ``"resend"``, or ``"sendgrid"`` (all HTTPS — required on Render free,
      which blocks SMTP ports).
    - ``smtp_*``  only read/required when ``sender == "smtp"``. ``smtp_from``
      is also the From address for Resend and SendGrid.
    - ``resend_api_key``  required when ``sender == "resend"``. Resend's
      sandbox sender (``onboarding@resend.dev``, used before a custom domain
      is verified) can only deliver to the Resend account's own email — every
      other recipient gets a 403. See ``sendgrid_api_key`` for the no-domain
      workaround.
    - ``sendgrid_api_key``  required when ``sender == "sendgrid"``. Unlike
      Resend's sandbox mode, SendGrid's **Single Sender Verification** (verify
      one address by clicking a confirmation link — no DNS/domain needed) can
      deliver to ANY recipient once that one address is verified, so this is
      the free, no-domain path to real delivery. Deliverability is weaker than
      a fully domain-authenticated sender (no SPF/DKIM alignment), which is
      fine at this volume but worth upgrading to domain auth later if it
      becomes an issue.
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
    resend_api_key: str | None = None
    sendgrid_api_key: str | None = None
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
            resend_api_key=os.getenv("EMAIL_RESEND_API_KEY"),
            sendgrid_api_key=os.getenv("EMAIL_SENDGRID_API_KEY"),
            owner_notification_email=os.getenv("OWNER_NOTIFICATION_EMAIL"),
        )
