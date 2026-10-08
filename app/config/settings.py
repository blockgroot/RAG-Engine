"""Typed settings loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_TIMEOUT = 60.0
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_EMBEDDING_BACKEND = "local"

DEFAULT_EMBEDDING_DIM = 1024

DEFAULT_CHUNK_SIZE = 256
DEFAULT_CHUNK_OVERLAP = 40
DEFAULT_CHUNK_TOKEN_BACKEND = "heuristic"
DEFAULT_MAX_CHUNK_CHARS = 4000

DEFAULT_VECTOR_STORE_BACKEND = "pgvector"

DEFAULT_RAG_TOP_K = 5
# When a question is pinned to a TAG (today: one Slack channel), the corpus is
# already narrow, and ranking it can only ever throw part of it away. These
# bound "small enough to read whole". Both must hold or the ordinary ranked
# retrieval runs instead, so the whole-scope read is never PARTIAL -- a summary
# that silently covered the newest N is the failure it exists to fix.
# Measured against a real channel: 25 threads = 38 chunks = ~27k chars, so the
# defaults leave roughly 2x headroom before a tenant falls off the path.
DEFAULT_RAG_SCOPE_WHOLE_MAX_CHUNKS = 120
DEFAULT_RAG_SCOPE_WHOLE_MAX_CHARS = 60000
DEFAULT_RAG_SIMILARITY_THRESHOLD = 0.35
DEFAULT_RAG_MAX_CONTEXT_CHARS = 6000
DEFAULT_RAG_MAX_ANSWER_TOKENS = 700
# Chunks either side of the best hits added to their block (0 = off). Benchmark 1
# found Handbook usually had the right document but only one 256-token piece of
# it, so the detail in the next paragraph was missing.
DEFAULT_RAG_NEIGHBOR_CHUNKS = 0
DEFAULT_MEMORY_FOLD_WAIT_SECONDS = 2.0
DEFAULT_RAG_FALLBACK_RESPONSE = (
    "I don't have information on that in the available policy documents."
)
DEFAULT_WORKSPACE_FALLBACK_RESPONSE = (
    "I don't have anything about that in this workspace's connected content."
)

DEFAULT_SLACK_FALLBACK_RESPONSE = (
    "I couldn't find that in the connected Slack channels. It may have been "
    "discussed in a channel that isn't connected, or before the synced history "
    "window."
)

DEFAULT_LINEAR_FALLBACK_RESPONSE = (
    "I couldn't find that in the connected Linear issues. It may not have been "
    "ingested yet, or no issue matches this question."
)

DEFAULT_NOTION_FALLBACK_RESPONSE = (
    "I couldn't find that in the connected Notion pages. It may not have been "
    "shared with the integration, or hasn't been ingested yet."
)
DEFAULT_DRIVE_FALLBACK_RESPONSE = (
    "I couldn't find that in the connected Google Drive documents. It may not "
    "be in the synced folder, or hasn't been ingested yet."
)

DEFAULT_GITHUB_FALLBACK_RESPONSE = (
    "I couldn't find that in the connected GitHub repositories. Try naming the "
    "repository, or check that it's included in this organization's GitHub "
    "installation."
)

DEFAULT_DB_POOL_MIN_SIZE = 1
DEFAULT_DB_POOL_MAX_SIZE = 10
DEFAULT_KEYWORD_CANDIDATE_LIMIT = 2000

DEFAULT_SOURCE_TYPE = "notion"

DEFAULT_MEMORY_RECENT_TURNS = 3

DEFAULT_CONTEXTUAL_ENABLED = True
DEFAULT_CONTEXTUAL_DEFER = True
DEFAULT_CONTEXTUAL_CONCURRENCY = 2
DEFAULT_CONTEXTUAL_MAX_CHUNKS = 200
DEFAULT_HYPOTHETICAL_QUESTIONS_ENABLED = True
DEFAULT_KEYWORD_EXTRACTION_ENABLED = True
DEFAULT_KEYWORD_EXTRACTION_TOP_N = 6
DEFAULT_EMBED_BATCH_SIZE = 16
DEFAULT_RETRIEVAL_HYBRID_ENABLED = True
DEFAULT_RETRIEVAL_RERANK_ENABLED = True
DEFAULT_RETRIEVAL_CANDIDATE_POOL = 16
DEFAULT_RETRIEVAL_RRF_K = 60
DEFAULT_RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"

DEFAULT_RETRIEVAL_REUSE_ENABLED = True
DEFAULT_RETRIEVAL_REUSE_THRESHOLD = 0.72

DEFAULT_TONE_CLASSIFY_ENABLED = True
DEFAULT_RECOVERY_ENABLED = True
DEFAULT_RECOVERY_MAX_QUERIES = 2

DEFAULT_AUDIT_ENABLED = False
AUDIT_BACKENDS = ("llm", "lettuce")
# Best balanced-accuracy cutoff measured on 60 RAGTruth-QA examples
# (scripts/bench_answer_check.py) was 0.61; re-measure on your own labels.
DEFAULT_AUDIT_LETTUCE_THRESHOLD = 0.6
DEFAULT_AUDIT_LETTUCE_TIMEOUT = 8.0

DEFAULT_DECOMPOSE_ENABLED = True

DEFAULT_REQUEST_DEADLINE_SECONDS = 45.0
DEFAULT_BUDGET_MIN_STAGE_SECONDS = 3.0

DEFAULT_QUERY_CACHE_ENABLED = True
DEFAULT_QUERY_CACHE_TTL_SECONDS = 300

DEFAULT_RATE_LIMIT_ENABLED = True
DEFAULT_RATE_LIMIT_CHAT_REQUESTS = 30
DEFAULT_RATE_LIMIT_AUTH_REQUESTS = 60
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60

DEFAULT_INGEST_MAX_DOCUMENT_CHARS = 2_000_000
DEFAULT_INGEST_MAX_CONTROL_CHAR_RATIO = 0.05

DEFAULT_INGEST_MEMORY_GUARD_ENABLED = True
DEFAULT_INGEST_MAX_RSS_MB = 400.0

DEFAULT_QUERY_NORM_ENABLED = True
DEFAULT_QUERY_NORM_MAX_EDIT_DISTANCE = 1
DEFAULT_QUERY_NORM_MIN_WORD_LENGTH = 4
DEFAULT_QUERY_NORM_CACHE_MAX_ORGS = 50

DEFAULT_WEB_SEARCH_ENABLED = True
DEFAULT_WEB_SEARCH_PROVIDER = "duckduckgo"
DEFAULT_WEB_SEARCH_MAX_RESULTS = 5
DEFAULT_WEB_SEARCH_TIMEOUT = 8.0

DEFAULT_SESSION_TTL_MINUTES = 60 * 24 * 30
DEFAULT_MAGIC_LINK_TTL_MINUTES = 10
DEFAULT_SIGNUP_ACTION_TTL_HOURS = 72

DEFAULT_API_HOST = "0.0.0.0"
DEFAULT_API_PORT = 8000

DEFAULT_EMAIL_SENDER = "console"

# OpenRouter's OpenAI-compatible endpoint (Multi-Model Selection).
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Groq's OpenAI-compatible endpoint. A second selectable backend, not a
# replacement: its free tier allows far more requests per day than
# OpenRouter's and it does not train on inputs, so the two are complementary.
DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"


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
    """LLM provider configuration."""

    model: str | None
    aux_model: str | None
    api_key: str | None
    base_url: str | None
    timeout: float = DEFAULT_TIMEOUT
    #: Separate endpoint for the AUX (ingest contextualization) provider.
    #: ``None`` — the default — means aux shares the main key and base_url,
    #: which is the behaviour that existed before these fields and is
    #: byte-identical today. Setting both moves background LLM work onto its
    #: own quota, which is the structural version of what ``LLMPacingSettings``
    #: can only approximate: separate endpoints cannot contend at all.
    aux_base_url: str | None = None
    aux_api_key: str | None = None
    #: WHICH provider the model is served by, named from a fixed list
    #: (``app/llm/adapters.py``). The adapter owns the base URL, so the
    #: provider is a validated choice rather than something implied by a
    #: free-text URL + model name. ``None`` = legacy: ``LLM_BASE_URL`` is used
    #: as-is, exactly as before this field existed, so an unmigrated deploy
    #: keeps working (with a startup warning).
    adapter: str | None = None
    #: The same for background work; with ``LLM_AUX_API_KEY`` it replaces
    #: ``LLM_AUX_BASE_URL``.
    aux_adapter: str | None = None
    #: What the boot-time config check does (``scripts/check_llm_config.py``):
    #: ``strict`` refuses to start on a config it can PROVE is wrong (unknown
    #: adapter, a model the provider does not list), ``warn`` only logs,
    #: ``off`` skips it. An unreachable model list is never a failure.
    config_check: str = "strict"

    @property
    def aux_has_own_endpoint(self) -> bool:
        """True when background work draws from a different rate limit.

        Requires BOTH an endpoint (a base_url or an adapter, which supplies
        one) and a key: an endpoint with the main key would send the wrong
        credential to the wrong host (a 401 on every contextualization,
        degrading silently to un-prefixed chunks), and a key with no endpoint
        would send a foreign key to the main endpoint. Half-configured
        therefore means "not configured", never "partly applied".
        """
        return bool((self.aux_base_url or self.aux_adapter) and self.aux_api_key)

    @classmethod
    def from_env(cls) -> "LLMSettings":
        check = (os.getenv("LLM_CONFIG_CHECK") or "strict").strip().lower()
        return cls(
            model=os.getenv("LLM_MODEL"),
            aux_model=os.getenv("LLM_AUX_MODEL") or None,
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
            timeout=float(os.getenv("LLM_TIMEOUT") or DEFAULT_TIMEOUT),
            aux_base_url=os.getenv("LLM_AUX_BASE_URL") or None,
            aux_api_key=os.getenv("LLM_AUX_API_KEY") or None,
            adapter=(os.getenv("LLM_ADAPTER") or "").strip().lower() or None,
            aux_adapter=(os.getenv("LLM_AUX_ADAPTER") or "").strip().lower() or None,
            config_check=check if check in ("strict", "warn", "off") else "strict",
        )


@dataclass(frozen=True)
class OpenRouterSettings:
    """Credentials for the user-selectable models (Multi-Model Selection).

    Deliberately separate from ``LLMSettings`` rather than extra fields on it:
    the deployment's default model and the models a member may *pick* are two
    different things with two different endpoints and keys. Auto keeps using
    ``LLMSettings``; only an explicit selection reaches OpenRouter, so a
    deployment with no key here still runs exactly as it does today — the
    picker is simply empty.
    """

    api_key: str | None
    base_url: str = DEFAULT_OPENROUTER_BASE_URL
    timeout: float = DEFAULT_TIMEOUT
    #: Sent to OpenRouter for app attribution; optional, purely cosmetic.
    referer: str | None = None
    title: str | None = None

    @property
    def enabled(self) -> bool:
        """No key = no selectable models. The dropdown hides itself."""
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "OpenRouterSettings":
        return cls(
            api_key=os.getenv("OPENROUTER_API_KEY") or None,
            base_url=os.getenv("OPENROUTER_BASE_URL") or DEFAULT_OPENROUTER_BASE_URL,
            timeout=float(os.getenv("OPENROUTER_TIMEOUT") or DEFAULT_TIMEOUT),
            referer=os.getenv("OPENROUTER_REFERER") or None,
            title=os.getenv("OPENROUTER_TITLE") or None,
        )


@dataclass(frozen=True)
class GroqSettings:
    """Credentials for Groq-hosted selectable models.

    Separate from ``OpenRouterSettings`` because they are different accounts
    with different keys and different quotas — and quota is the reason both
    exist. OpenRouter's free tier allows 50 requests/day account-wide; Groq's
    allows thousands. Offering models from both means one being exhausted or
    rate-limited does not empty the picker.

    Groq also states it does not train on inputs, so unlike OpenRouter's free
    endpoints there is no per-request data policy to negotiate — which is why
    no routing preferences are sent to it (see ``app/llm/routed.py``).
    """

    api_key: str | None
    base_url: str = DEFAULT_GROQ_BASE_URL
    timeout: float = DEFAULT_TIMEOUT

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @classmethod
    def from_env(cls) -> "GroqSettings":
        return cls(
            api_key=os.getenv("GROQ_API_KEY") or None,
            base_url=os.getenv("GROQ_BASE_URL") or DEFAULT_GROQ_BASE_URL,
            timeout=float(os.getenv("GROQ_TIMEOUT") or DEFAULT_TIMEOUT),
        )


@dataclass(frozen=True)
class EmbeddingSettings:
    """Embedding provider configuration."""

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
    """Postgres and pgvector configuration."""

    url: str | None
    embedding_dim: int = DEFAULT_EMBEDDING_DIM
    pool_min_size: int = DEFAULT_DB_POOL_MIN_SIZE
    pool_max_size: int = DEFAULT_DB_POOL_MAX_SIZE
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
    """RAG retrieval and generation settings."""

    top_k: int = DEFAULT_RAG_TOP_K
    similarity_threshold: float = DEFAULT_RAG_SIMILARITY_THRESHOLD
    fallback_response: str = DEFAULT_RAG_FALLBACK_RESPONSE
    max_context_chars: int = DEFAULT_RAG_MAX_CONTEXT_CHARS
    max_answer_tokens: int | None = DEFAULT_RAG_MAX_ANSWER_TOKENS
    scope_whole_max_chunks: int = DEFAULT_RAG_SCOPE_WHOLE_MAX_CHUNKS
    scope_whole_max_chars: int = DEFAULT_RAG_SCOPE_WHOLE_MAX_CHARS
    neighbor_chunks: int = DEFAULT_RAG_NEIGHBOR_CHUNKS
    # Tell the model not to attach one document's facts to another's subject
    # (Benchmark 1: wrong-document facts were the second-largest loss).
    focus_rule: bool = False
    # Wide reads (Benchmark 2: 17 of the 33 questions Onyx won needed 6-8
    # documents and Handbook read 2-5). When several documents score close to
    # the best one, read up to ``wide_max_hits`` passages, at most
    # ``wide_per_doc`` from each, so the extra room goes to OTHER documents.
    # Driven by the reranker's scores, never by the question's wording. Off
    # unless ``wide_max_hits`` > ``top_k`` and ``wide_doc_ratio`` > 0.
    wide_max_hits: int = 0
    wide_doc_ratio: float = 0.0
    wide_per_doc: int = 1
    # 0 = scale ``max_context_chars`` by wide_max_hits / top_k.
    wide_max_context_chars: int = 0
    # Answer the part CONTEXT covers and say what is missing, instead of the
    # fallback (Benchmark 2: 6 refusals had the right document in the prompt).
    partial_rule: bool = False
    # When blocks disagree, prefer the newer or superseding one and say so.
    conflict_rule: bool = False

    @property
    def ranked_max_hits(self) -> int:
        """The most hits a RANKED retrieval can return; more means a whole read."""
        return max(self.top_k, self.wide_max_hits if self.wide_doc_ratio > 0 else 0)

    def context_chars_for(self, n_hits: int) -> int:
        """Prompt budget for ``n_hits`` ranked hits: the usual one, or a wide read's."""
        if n_hits <= self.top_k:
            return self.max_context_chars
        return self.wide_max_context_chars or self.max_context_chars * self.wide_max_hits // self.top_k

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
            scope_whole_max_chunks=int(
                os.getenv("RAG_SCOPE_WHOLE_MAX_CHUNKS") or DEFAULT_RAG_SCOPE_WHOLE_MAX_CHUNKS
            ),
            scope_whole_max_chars=int(
                os.getenv("RAG_SCOPE_WHOLE_MAX_CHARS") or DEFAULT_RAG_SCOPE_WHOLE_MAX_CHARS
            ),
            similarity_threshold=float(
                os.getenv("RAG_SIMILARITY_THRESHOLD") or DEFAULT_RAG_SIMILARITY_THRESHOLD
            ),
            fallback_response=os.getenv("RAG_FALLBACK_RESPONSE") or DEFAULT_RAG_FALLBACK_RESPONSE,
            max_context_chars=int(
                os.getenv("RAG_MAX_CONTEXT_CHARS") or DEFAULT_RAG_MAX_CONTEXT_CHARS
            ),
            max_answer_tokens=max_answer_tokens,
            neighbor_chunks=int(os.getenv("RAG_NEIGHBOR_CHUNKS") or DEFAULT_RAG_NEIGHBOR_CHUNKS),
            focus_rule=env_bool("RAG_FOCUS_RULE", False),
            wide_max_hits=int(os.getenv("RAG_WIDE_MAX_HITS") or 0),
            wide_doc_ratio=float(os.getenv("RAG_WIDE_DOC_RATIO") or 0.0),
            wide_per_doc=int(os.getenv("RAG_WIDE_PER_DOC") or 1),
            wide_max_context_chars=int(os.getenv("RAG_WIDE_MAX_CONTEXT_CHARS") or 0),
            partial_rule=env_bool("RAG_PARTIAL_RULE", False),
            conflict_rule=env_bool("RAG_CONFLICT_RULE", False),
        )


@dataclass(frozen=True)
class WorkspaceAgentSettings:
    """Workspace-agent-specific settings."""

    fallback_response: str = DEFAULT_WORKSPACE_FALLBACK_RESPONSE

    @classmethod
    def from_env(cls) -> "WorkspaceAgentSettings":
        return cls(
            fallback_response=os.getenv("WORKSPACE_FALLBACK_RESPONSE")
            or DEFAULT_WORKSPACE_FALLBACK_RESPONSE,
        )


@dataclass(frozen=True)
class SlackAgentSettings:
    """Slack-agent-specific settings."""

    fallback_response: str = DEFAULT_SLACK_FALLBACK_RESPONSE

    @classmethod
    def from_env(cls) -> "SlackAgentSettings":
        return cls(
            fallback_response=os.getenv("SLACK_FALLBACK_RESPONSE")
            or DEFAULT_SLACK_FALLBACK_RESPONSE,
        )


@dataclass(frozen=True)
class LinearAgentSettings:
    """Linear-agent-specific settings."""

    fallback_response: str = DEFAULT_LINEAR_FALLBACK_RESPONSE

    @classmethod
    def from_env(cls) -> "LinearAgentSettings":
        return cls(
            fallback_response=os.getenv("LINEAR_FALLBACK_RESPONSE")
            or DEFAULT_LINEAR_FALLBACK_RESPONSE,
        )


@dataclass(frozen=True)
class NotionAgentSettings:
    """Notion-agent-specific settings."""

    fallback_response: str = DEFAULT_NOTION_FALLBACK_RESPONSE

    @classmethod
    def from_env(cls) -> "NotionAgentSettings":
        return cls(
            fallback_response=os.getenv("NOTION_AGENT_FALLBACK_RESPONSE")
            or DEFAULT_NOTION_FALLBACK_RESPONSE,
        )


@dataclass(frozen=True)
class DriveAgentSettings:
    """Drive-agent-specific settings."""

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
# Reading Google Forms RESPONSES needs its own scope, and it is deliberately
# NOT in the default above: adding a scope invalidates nothing technically, but
# an existing token does not have it, so every already-connected tenant would
# have to reconnect before Drive worked again the next time consent was
# re-checked. Opt in with GOOGLE_FORMS_ENABLED=true, which appends it here --
# the one place scopes are assembled -- and then reconnect Google once.
#
# `drive.readonly` already covers FINDING the forms (the Forms API has no
# listing endpoint), so this is the only addition needed.
GOOGLE_FORMS_SCOPE = "https://www.googleapis.com/auth/forms.responses.readonly"
# Expanding a Drive `group:` grant into the people actually in that group needs
# the Admin SDK Directory API, which has its own scope and its own hard
# requirement: the CONNECTED account must be a Google Workspace admin (or hold
# a delegated role with group-read privilege). A non-admin connection simply
# 403s, which is why this fails closed to "no groups" rather than erroring.
#
# Out of the default for the same reason as Forms: an already-connected tenant
# does not have it, so defaulting it on would force every tenant to reconnect
# Google. Opt in with GOOGLE_GROUPS_ENABLED=true, then reconnect once.
GOOGLE_GROUPS_SCOPE = "https://www.googleapis.com/auth/admin.directory.group.readonly"
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
    #: Whether form-response reading (sentiment charts) is enabled. Off by
    #: default because turning it on requires every tenant to reconnect
    #: Google, which is a deploy decision rather than a code one.
    forms_enabled: bool = False
    #: Whether a Drive ``group:`` grant is expanded into its members for
    #: document-level access filtering. Off by default: it needs an extra OAuth
    #: scope (so every tenant reconnects) AND a Workspace-admin connection, so
    #: switching it on is a deploy decision rather than a code one. Off, a
    #: group-shared file stays withheld from everyone but the connecting
    #: account -- fail-closed, which is the behaviour that shipped.
    groups_enabled: bool = False

    @classmethod
    def from_env(cls) -> "GoogleSettings":
        forms_enabled = env_bool("GOOGLE_FORMS_ENABLED", False)
        groups_enabled = env_bool("GOOGLE_GROUPS_ENABLED", False)
        scopes = os.getenv("GOOGLE_OAUTH_SCOPES", DEFAULT_GOOGLE_OAUTH_SCOPES)
        # Appended rather than replacing the default, and only when asked, so
        # an explicit GOOGLE_OAUTH_SCOPES override still gets Forms access if
        # the flag is on -- otherwise the two settings would silently disagree.
        if forms_enabled and GOOGLE_FORMS_SCOPE not in scopes:
            scopes = f"{scopes} {GOOGLE_FORMS_SCOPE}"
        if groups_enabled and GOOGLE_GROUPS_SCOPE not in scopes:
            scopes = f"{scopes} {GOOGLE_GROUPS_SCOPE}"
        return cls(
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            redirect_uri=os.getenv("GOOGLE_REDIRECT_URI"),
            scopes=scopes,
            forms_enabled=forms_enabled,
            groups_enabled=groups_enabled,
            max_walk_folders=int(
                os.getenv("GOOGLE_MAX_WALK_FOLDERS") or DEFAULT_GOOGLE_MAX_WALK_FOLDERS
            ),
            max_documents=int(
                os.getenv("GOOGLE_MAX_DOCUMENTS") or DEFAULT_GOOGLE_MAX_DOCUMENTS
            ),
        )


# Phase 1 of Slack Integration Plan (git history: docs/plans/2026-08-17-slack-integration.md):
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
    "users:read,users:read.email,"
    # Ask-in-Slack: `chat:write` is the only scope the bot needs to ANSWER
    # (reading DMs and mentions is already covered above). Adding it means
    # every existing connection must reconnect once to pick it up -- Slack
    # grants scopes at install time, never retroactively.
    "chat:write,app_mentions:read"
)

# Phase 2 bounds (plan §6/D11 — "bound the walk itself" discipline, same as
# GOOGLE_MAX_WALK_FOLDERS/GOOGLE_MAX_DOCUMENTS above and the Notion fetch-size
# bound in CLAUDE.md §4). A busy channel's full history is not safe to embed
# unbounded, so every knob here caps something rather than trusting input size.
DEFAULT_SLACK_BACKFILL_DAYS = 90
# 40 was too high: "Deploy is frozen till Monday" is 28 characters, so a real
# standalone announcement was dropped from ingestion AND from change detection
# — the Sources check truthfully said "up to date" while the channel had new
# content, and the answer stayed stale because the message was never indexed.
# 15 still excludes the noise this bound exists for ("ok", "thanks", "+1",
# an emoji) without deciding that a short sentence is not information.
DEFAULT_SLACK_MIN_THREAD_CHARS = 15
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
    backfill_days: int = DEFAULT_SLACK_BACKFILL_DAYS
    min_thread_chars: int = DEFAULT_SLACK_MIN_THREAD_CHARS
    max_thread_messages: int = DEFAULT_SLACK_MAX_THREAD_MESSAGES
    max_documents_per_sync: int = DEFAULT_SLACK_MAX_DOCUMENTS_PER_SYNC
    # Slack signs every event it sends us; without this we cannot tell a real
    # event from anyone who has guessed the URL. Unset DISABLES the events
    # route entirely (same posture as INTERNAL_TICK_SECRET) rather than
    # accepting unverified posts.
    signing_secret: str | None = None

    @classmethod
    def from_env(cls) -> "SlackSettings":
        return cls(
            client_id=os.getenv("SLACK_CLIENT_ID"),
            client_secret=os.getenv("SLACK_CLIENT_SECRET"),
            redirect_uri=os.getenv("SLACK_REDIRECT_URI"),
            scopes=os.getenv("SLACK_BOT_SCOPES", DEFAULT_SLACK_BOT_SCOPES),
            signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
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
    """GitHub App connect settings."""

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
            private_key=raw_key.replace("\\n", "\n") if raw_key else None,
        )


@dataclass(frozen=True)
class GitHubAgentSettings:
    """GitHub-agent-specific settings."""

    fallback_response: str = DEFAULT_GITHUB_FALLBACK_RESPONSE
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
    """Bounds for live GitHub reads."""

    enabled: bool = True
    timeout: float = 10.0
    readme_max_bytes: int = 40_000
    patch_max_bytes: int = 4_000
    max_files_per_commit: int = 25
    max_commits: int = 20
    # Charts read pull requests, and reviews cost ONE call per pull request --
    # so the pull-request set is what actually bounds the API spend. 100 pull
    # requests + their reviews is ~101 calls per repo per sync, against
    # GitHub's 5,000/hour installation limit.
    #: A long-lived repo accumulates stale branches; a prompt full of dead
    #: names answers nothing.
    max_branches: int = 50
    max_pull_requests: int = 100
    # Reviews are fetched only for this many of them, newest first. A chart of
    # who reviews is stable well before 100 samples, and this is the difference
    # between ~30 calls and ~130.
    max_reviewed_pull_requests: int = 30
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
            max_branches=int(os.getenv("GITHUB_MAX_BRANCHES", "50")),
            max_pull_requests=int(os.getenv("GITHUB_MAX_PULL_REQUESTS", "100")),
            max_reviewed_pull_requests=int(
                os.getenv("GITHUB_MAX_REVIEWED_PULL_REQUESTS", "30")
            ),
            max_attempts=int(os.getenv("GITHUB_LIVE_MAX_ATTEMPTS", "3")),
        )


@dataclass(frozen=True)
class MemorySettings:
    """Conversation-memory sizing."""

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
    """Retrieval-reuse settings for conversation follow-ups."""

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
    """Question-tone classification settings."""

    enabled: bool = DEFAULT_TONE_CLASSIFY_ENABLED

    @classmethod
    def from_env(cls) -> "ToneSettings":
        return cls(
            enabled=env_bool("TONE_CLASSIFY_ENABLED", DEFAULT_TONE_CLASSIFY_ENABLED),
        )


@dataclass(frozen=True)
class RecoverySettings:
    """Bounded retrieval-recovery settings."""

    enabled: bool = DEFAULT_RECOVERY_ENABLED
    max_queries: int = DEFAULT_RECOVERY_MAX_QUERIES
    # Also search with the alternative wordings up front, not only after a
    # failure: a document worded differently from the question can be missed
    # while the search still scores "good enough" to skip recovery.
    proactive: bool = False

    @classmethod
    def from_env(cls) -> "RecoverySettings":
        return cls(
            enabled=env_bool("RECOVERY_ENABLED", DEFAULT_RECOVERY_ENABLED),
            max_queries=int(os.getenv("RECOVERY_MAX_QUERIES") or DEFAULT_RECOVERY_MAX_QUERIES),
            proactive=env_bool("RECOVERY_PROACTIVE", False),
        )


#: Hosts an answer may link to even when the link was not in its sources: the
#: connected tools' own domains. Query strings are still cut (see
#: `security/links.py`), so an allowlisted host cannot carry data out.
DEFAULT_LINK_ALLOWLIST = (
    "notion.so", "notion.site", "docs.google.com", "drive.google.com",
    "slack.com", "linear.app", "github.com",
)


@dataclass(frozen=True)
class SecuritySettings:
    """Deterministic prompt-injection controls (git history: docs/plans/2026-09-28-prompt-injection-defense.md).

    - ``link_allowlist``  `SECURITY_LINK_ALLOWLIST`, comma-separated hosts;
      empty string = only links that appear verbatim in the sources.
    """

    link_allowlist: tuple[str, ...] = DEFAULT_LINK_ALLOWLIST

    @classmethod
    def from_env(cls) -> "SecuritySettings":
        raw = os.getenv("SECURITY_LINK_ALLOWLIST")
        if raw is None:
            return cls()
        hosts = tuple(h.strip().lower().lstrip(".") for h in raw.split(",") if h.strip())
        return cls(link_allowlist=hosts)


GUARD_MODES = ("off", "shadow", "enforce")
GUARD_BACKENDS = ("prompt_guard", "safeguard")
DEFAULT_GUARD_MODEL = "meta-llama/llama-prompt-guard-2-86m"


@dataclass(frozen=True)
class GuardSettings:
    """Injection scoring of untrusted text (Phase 2+ of the injection plan).

    Llama Prompt Guard 2 (86M) on Groq's free tier, with the ``GROQ_API_KEY``
    the model picker already uses. 86M, not 22M: measured, 22M scored a
    Spanish injection 0.43 where 86M scored 0.999. Free-tier limits are 14.4K
    requests/day and 15K tokens/min — a big first sync overflows the minute
    budget, which leaves those chunks unscored for the backfill, never fails
    the ingest.

    - ``backend``  ``prompt_guard`` (default: Llama Prompt Guard 2, 14.4K
      requests/day, catches override WORDING) or ``safeguard``
      (`gpt-oss-safeguard-20b` with our policy, 1,000/day, catches ACTION
      injections Prompt Guard misses -- see ``guard/safeguard.py``). Each chunk
      stores which model scored it, so switching rescans via the backfill.
    - ``mode``  ``off`` (default: no calls at all), ``shadow`` (score, store,
      log — never change an answer), ``enforce`` (Phase 3).
    - ``threshold``  a chunk scoring at or above this is "flagged". The RAW
      score is stored, so changing this needs no rescan. 0.9, not 0.5:
      measured, a real IT page ("Ignore the old reset email and use the new
      portal") scored 0.70 while a planted "Note to AI assistants: ignore prior
      instructions" scored 0.999.
    - ``backfill_batch``  chunks scored per tick for rows ingest left NULL.
    - ``answer_check``  `GUARD_ANSWER_CHECK`: also run the finished answer past
      `gpt-oss-safeguard-20b` (`guard/moderation.py`). Off by default: its free
      tier is 1,000 requests/day, one per answer.
    """

    mode: str = "off"
    backend: str = "prompt_guard"
    model: str = DEFAULT_GUARD_MODEL
    threshold: float = 0.9
    timeout: float = 5.0
    backfill_batch: int = 40
    answer_check: bool = False

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @classmethod
    def from_env(cls) -> "GuardSettings":
        from ..core.exceptions import ConfigurationError

        mode = (os.getenv("GUARD_MODE") or "off").strip().lower()
        if mode not in GUARD_MODES:
            raise ConfigurationError(f"GUARD_MODE={mode!r} is not one of {', '.join(GUARD_MODES)}")
        backend = (os.getenv("GUARD_BACKEND") or "prompt_guard").strip().lower()
        if backend not in GUARD_BACKENDS:
            raise ConfigurationError(
                f"GUARD_BACKEND={backend!r} is not one of {', '.join(GUARD_BACKENDS)}"
            )
        return cls(
            mode=mode,
            backend=backend,
            model=os.getenv("GUARD_MODEL") or DEFAULT_GUARD_MODEL,
            threshold=float(os.getenv("GUARD_THRESHOLD") or 0.9),
            timeout=float(os.getenv("GUARD_TIMEOUT") or 5.0),
            backfill_batch=int(os.getenv("GUARD_BACKFILL_BATCH") or 40),
            answer_check=env_bool("GUARD_ANSWER_CHECK", False),
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
    - ``backend``  ``llm`` (the prompt above, the default) or ``lettuce`` — a
      LettuceDetect span classifier behind our own HTTP endpoint
      (``deploy/lettucedetect-space/``). Measured on RAGTruth-QA it rejected
      1/30 grounded answers where the LLM audit rejected 12/30, and it spends
      none of the 15 rpm LLM quota. The endpoint receives tenant chunks, so it
      must be one WE run (a private Space), never a public demo.
    - ``lettuce_threshold``  a flagged span at or above this confidence
      downgrades the answer.
    """

    enabled: bool = DEFAULT_AUDIT_ENABLED
    backend: str = "llm"
    lettuce_url: str | None = None
    lettuce_token: str | None = None
    lettuce_threshold: float = DEFAULT_AUDIT_LETTUCE_THRESHOLD
    lettuce_timeout: float = DEFAULT_AUDIT_LETTUCE_TIMEOUT

    @classmethod
    def from_env(cls) -> "AuditSettings":
        from ..core.exceptions import ConfigurationError

        backend = (os.getenv("RAG_AUDIT_BACKEND") or "llm").strip().lower()
        if backend not in AUDIT_BACKENDS:
            raise ConfigurationError(
                f"RAG_AUDIT_BACKEND={backend!r} is not one of {', '.join(AUDIT_BACKENDS)}"
            )
        url = (os.getenv("RAG_AUDIT_LETTUCE_URL") or "").strip() or None
        if backend == "lettuce" and not url:
            raise ConfigurationError("RAG_AUDIT_BACKEND=lettuce needs RAG_AUDIT_LETTUCE_URL")
        return cls(
            enabled=env_bool("RAG_AUDIT_ENABLED", DEFAULT_AUDIT_ENABLED),
            backend=backend,
            lettuce_url=url,
            lettuce_token=os.getenv("RAG_AUDIT_LETTUCE_TOKEN") or None,
            lettuce_threshold=float(
                os.getenv("RAG_AUDIT_LETTUCE_THRESHOLD") or DEFAULT_AUDIT_LETTUCE_THRESHOLD
            ),
            lettuce_timeout=float(
                os.getenv("RAG_AUDIT_LETTUCE_TIMEOUT") or DEFAULT_AUDIT_LETTUCE_TIMEOUT
            ),
        )


@dataclass(frozen=True)
class AttachmentSettings:
    """Bounds for an in-chat file attachment.

    Every value here is a CEILING, not a target. The upload is read into
    memory to be parsed, this runs on a 512MB box (CLAUDE.md §5), so
    ``max_bytes`` protects the box; the rest protect the PROMPT, which is the
    scarcer resource.

    **Two ways a file reaches the model, chosen by size.** A short file is
    injected whole (``inline_char_budget``) -- simplest thing that works, and
    the model sees everything. A long one is injected as a ``preview_chars``
    head per file plus a ``read_file`` tool it can call to page through the
    rest. The alternative was a hard truncation, which answers a 300-page
    contract from its first 60k characters and presents that as an answer
    about the contract.
    """

    #: Read into memory to parse, so this is a memory bound as much as a
    #: product one. Deliberately NOT raised alongside the char budget: a 100MB
    #: PDF is ~200MB resident once pypdf has it, which is the whole box.
    max_bytes: int = 10 * 1024 * 1024
    #: What we STORE. Large because paging makes a long document usable
    #: rather than truncated; a 400k-char string is 0.4MB in Postgres and in
    #: memory, which is nothing next to the byte budget above.
    max_chars: int = 400_000
    max_pdf_pages: int = 1_000
    max_csv_rows: int = 20_000
    #: Per conversation. Every attachment rides on EVERY turn of that chat,
    #: so an unbounded count is an unbounded prompt.
    max_per_conversation: int = 5

    #: At or below this TOTAL across all attachments, the text goes into the
    #: prompt whole and no tool is offered. ~3k tokens.
    inline_char_budget: int = 12_000
    #: The head shown per file when the total is over that budget. Enough to
    #: identify the document and its structure, not to answer from.
    preview_chars: int = 500
    #: One `read_file` call returns at most this much. Onyx's number.
    max_read_chars: int = 16_000
    #: Calls honoured in the single tool round. Bounds the worst case at
    #: max_reads * max_read_chars of added context.
    max_reads: int = 4

    #: Reject an upload whose extracted text exceeds this many TOKENS. Onyx's
    #: `file_token_count_threshold_k`, and the gate they apply that we did not:
    #: characters bound the DATABASE, tokens bound the PROMPT, and the prompt
    #: is what actually fails. Counting at upload means the member is told
    #: immediately instead of discovering it as a thin answer later.
    #:
    #: 0 disables the gate, matching their "0 means no limit". Deliberately
    #: generous by default -- paging already makes a long file usable, so this
    #: is the ceiling above which paging stops being worth it, not a target.
    max_tokens: int = 120_000

    @classmethod
    def from_env(cls) -> "AttachmentSettings":
        return cls(
            max_bytes=int(os.getenv("ATTACHMENT_MAX_BYTES") or 10 * 1024 * 1024),
            max_chars=int(os.getenv("ATTACHMENT_MAX_CHARS") or 400_000),
            max_pdf_pages=int(os.getenv("ATTACHMENT_MAX_PDF_PAGES") or 1_000),
            max_csv_rows=int(os.getenv("ATTACHMENT_MAX_CSV_ROWS") or 20_000),
            max_per_conversation=int(os.getenv("ATTACHMENT_MAX_PER_CONVERSATION") or 5),
            inline_char_budget=int(os.getenv("ATTACHMENT_INLINE_CHARS") or 12_000),
            preview_chars=int(os.getenv("ATTACHMENT_PREVIEW_CHARS") or 500),
            max_read_chars=int(os.getenv("ATTACHMENT_MAX_READ_CHARS") or 16_000),
            max_reads=int(os.getenv("ATTACHMENT_MAX_READS") or 4),
            max_tokens=int(os.getenv("ATTACHMENT_MAX_TOKENS") or 120_000),
        )


@dataclass(frozen=True)
class CloudinarySettings:
    """Where an uploaded attachment's BYTES and extracted text are stored.

    The shape is Onyx's `FileStore` (`backend/onyx/file_store/file_store.py`):
    the object store holds BOTH the original upload and a second object
    carrying the extracted plaintext, while Postgres keeps only the metadata
    row. Reading the cached plaintext is what stops every turn of a chat from
    re-parsing a PDF (their `_get_or_extract_plaintext`).

    There is no `base.py`/`factory.py` here and there is deliberately no second
    backend: CLAUDE.md §2 asks for that shape when a capability HAS another
    implementation to abstract over, and one object store is not two. Onyx
    carries four (S3, GCS, Azure, Postgres) because they ship to four kinds of
    customer; we ship to one.

    **Assets are uploaded `authenticated`, never public.** A Cloudinary URL is
    otherwise permanent and readable by anyone it reaches, which would make a
    tenant's contract a link away from the whole internet and would outlive the
    conversation it belongs to. Signed URLs expire, so a forwarded one stops
    working -- that is the property that makes an object store acceptable for
    this content at all.
    """

    cloud_name: str
    api_key: str
    api_secret: str
    #: Everything this app writes lives under one prefix, so a bucket shared
    #: with anything else stays separable and a prefix listing is a complete
    #: inventory of what we put there.
    folder: str = "handbook/attachments"
    #: Lifetime of a signed URL. Minutes, not hours: the only consumer is a
    #: download the member just asked for.
    signed_url_ttl_seconds: int = 600

    @property
    def configured(self) -> bool:
        """False when any credential is missing.

        BOTH or neither, the `aux_has_own_endpoint` posture: a half-configured
        store fails on every upload with an auth error that reads as a broken
        feature rather than as a missing setting.
        """
        return bool(self.cloud_name and self.api_key and self.api_secret)

    @classmethod
    def from_env(cls) -> "CloudinarySettings":
        return cls(
            cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
            api_key=os.getenv("CLOUDINARY_API_KEY", ""),
            api_secret=os.getenv("CLOUDINARY_API_SECRET", ""),
            folder=os.getenv("CLOUDINARY_FOLDER") or "handbook/attachments",
            signed_url_ttl_seconds=int(
                os.getenv("CLOUDINARY_SIGNED_URL_TTL_SECONDS") or 600
            ),
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
    """Ingestion worker memory admission settings."""

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
    """Corpus-vocabulary spelling correction before retrieval."""

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
    """Web-search tool settings."""

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
    """Ingest-time contextual retrieval settings."""

    enabled: bool = DEFAULT_CONTEXTUAL_ENABLED
    defer: bool = DEFAULT_CONTEXTUAL_DEFER
    concurrency: int = DEFAULT_CONTEXTUAL_CONCURRENCY
    max_chunks: int = DEFAULT_CONTEXTUAL_MAX_CHUNKS
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
class KeywordExtractionSettings:
    """Keyword-extraction settings for ingest-time chunk enrichment."""

    enabled: bool = DEFAULT_KEYWORD_EXTRACTION_ENABLED
    top_n: int = DEFAULT_KEYWORD_EXTRACTION_TOP_N

    @classmethod
    def from_env(cls) -> "KeywordExtractionSettings":
        return cls(
            enabled=env_bool("INGEST_KEYWORDS_ENABLED", DEFAULT_KEYWORD_EXTRACTION_ENABLED),
            top_n=_env_positive_int("INGEST_KEYWORDS_TOP_N", DEFAULT_KEYWORD_EXTRACTION_TOP_N),
        )


@dataclass(frozen=True)
class RetrievalSettings:
    """Query-time retrieval settings."""

    hybrid_enabled: bool = DEFAULT_RETRIEVAL_HYBRID_ENABLED
    rerank_enabled: bool = DEFAULT_RETRIEVAL_RERANK_ENABLED
    candidate_pool: int = DEFAULT_RETRIEVAL_CANDIDATE_POOL
    rrf_k: int = DEFAULT_RETRIEVAL_RRF_K
    # Drop a reranked hit scoring below this fraction of the best hit's
    # reranker score (0 = off). Benchmark 1: the answer model mixed in facts
    # from weak, off-topic passages. Uncalibrated, so off until the logged
    # ``rerank_scores`` show where gold and distractor passages separate.
    rerank_min_ratio: float = 0.0

    @classmethod
    def from_env(cls) -> "RetrievalSettings":
        return cls(
            hybrid_enabled=env_bool("RETRIEVAL_HYBRID_ENABLED", DEFAULT_RETRIEVAL_HYBRID_ENABLED),
            rerank_enabled=env_bool("RETRIEVAL_RERANK_ENABLED", DEFAULT_RETRIEVAL_RERANK_ENABLED),
            candidate_pool=int(
                os.getenv("RETRIEVAL_CANDIDATE_POOL") or DEFAULT_RETRIEVAL_CANDIDATE_POOL
            ),
            rrf_k=int(os.getenv("RETRIEVAL_RRF_K") or DEFAULT_RETRIEVAL_RRF_K),
            rerank_min_ratio=float(os.getenv("RETRIEVAL_RERANK_MIN_RATIO") or 0.0),
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


# Prompt-Driven Activity Scheduler. The poll interval is generous because
# schedulers are weekly/monthly: polling every few seconds like the ingestion
# queue would spend a query a second to catch a row that becomes due once a
# week. batch_size bounds how many run per tick so a burst of simultaneously
# due schedulers can't monopolise the shared worker thread.
DEFAULT_SCHEDULER_POLL_SECONDS = 300
DEFAULT_SCHEDULER_MAX_ATTEMPTS = 3
DEFAULT_SCHEDULER_BATCH_SIZE = 5


@dataclass(frozen=True)
class SchedulerSettings:
    """Configuration for the recurring activity-report scheduler."""

    enabled: bool = True
    poll_seconds: int = DEFAULT_SCHEDULER_POLL_SECONDS
    max_attempts: int = DEFAULT_SCHEDULER_MAX_ATTEMPTS
    batch_size: int = DEFAULT_SCHEDULER_BATCH_SIZE

    @classmethod
    def from_env(cls) -> "SchedulerSettings":
        return cls(
            enabled=(os.getenv("SCHEDULER_ENABLED", "true").strip().lower()
                     not in {"false", "0", "no"}),
            poll_seconds=int(
                os.getenv("SCHEDULER_POLL_SECONDS") or DEFAULT_SCHEDULER_POLL_SECONDS
            ),
            max_attempts=int(
                os.getenv("SCHEDULER_MAX_ATTEMPTS") or DEFAULT_SCHEDULER_MAX_ATTEMPTS
            ),
            batch_size=int(
                os.getenv("SCHEDULER_BATCH_SIZE") or DEFAULT_SCHEDULER_BATCH_SIZE
            ),
        )


# Automatic sync. Freshness must not depend on anyone pressing a button, so
# every connection is re-synced on an interval even when no webhook arrives.
#
# The interval is a FLOOR, not the plan: Slack/Linear/Notion push an event and
# get synced within one tick, while Drive can only ever be polled (its push
# notifications require a Google-verified domain, which a *.onrender.com host
# cannot be), and a webhook delivered while the free box was asleep is lost.
#
# 1h, not 6h, because the interval only governs how often we DIFF, not how much
# we ingest: `pipeline._plan_refs` splits the listing into new/updated/unchanged
# and an unchanged document never reaches `fetch_document`, so it costs no
# embedding and no contextualization call. A no-op sync is one `list_documents`
# plus one `documents` SELECT. The LLM/embedding spend per day is set by how
# often content actually changes, which an interval cannot alter -- so shortening
# it buys freshness at listing prices. What it does bound is THROUGHPUT:
# batch_size connections per tick (a tick every 10 min) is the ceiling, so an
# interval of 1h means at most batch_size * 6 connections can be kept on
# schedule; past that the oldest-first ordering starts to slip and the effective
# interval stretches. Raise batch_size, not the interval, when that day comes.
#
# batch_size bounds how many connections one tick may enqueue: a 40-connection
# org must not turn a single tick into 40 simultaneous ingests on a 512MB box.
DEFAULT_AUTO_SYNC_INTERVAL_HOURS = 1
DEFAULT_AUTO_SYNC_BATCH_SIZE = 5


@dataclass(frozen=True)
class AutoSyncSettings:
    """Configuration for background connection syncing."""

    enabled: bool = True
    interval_hours: int = DEFAULT_AUTO_SYNC_INTERVAL_HOURS
    batch_size: int = DEFAULT_AUTO_SYNC_BATCH_SIZE
    #: Shared secret for ``POST /internal/tick``. Unset disables the endpoint
    #: entirely rather than leaving it open — an unauthenticated tick is a free
    #: way for anyone to spend the org's provider quota.
    tick_secret: str | None = None

    @classmethod
    def from_env(cls) -> "AutoSyncSettings":
        return cls(
            enabled=(os.getenv("AUTO_SYNC_ENABLED", "true").strip().lower()
                     not in {"false", "0", "no"}),
            interval_hours=max(
                1,
                int(
                    os.getenv("AUTO_SYNC_INTERVAL_HOURS")
                    or DEFAULT_AUTO_SYNC_INTERVAL_HOURS
                ),
            ),
            batch_size=max(
                1,
                int(
                    os.getenv("AUTO_SYNC_BATCH_SIZE") or DEFAULT_AUTO_SYNC_BATCH_SIZE
                ),
            ),
            tick_secret=(os.getenv("INTERNAL_TICK_SECRET") or None),
        )



@dataclass(frozen=True)
class WebhookSettings:
    """Push receivers for Notion, Linear and Drive (`app/api/webhooks.py`).

    Every field unset means that receiver is CLOSED (404) and the provider is
    polled on the auto-sync interval exactly as before -- the INTERNAL_TICK_SECRET
    posture: an unconfigured secret closes a route rather than leaving an
    unauthenticated one open.
    """

    #: The `verification_token` Notion POSTs once when the subscription is
    #: created; it is also the HMAC key for `X-Notion-Signature`.
    notion_verification_token: str | None = None
    #: The signing secret of the Linear webhook (OAuth-app webhook settings, or
    #: a workspace webhook), used for `Linear-Signature`.
    linear_secret: str | None = None
    #: Public HTTPS base URL of THIS API, where Google delivers Drive push
    #: notifications (`<base>/webhooks/google`). Unset = Drive is poll-only.
    drive_push_base_url: str | None = None

    @classmethod
    def from_env(cls) -> "WebhookSettings":
        base = (os.getenv("DRIVE_PUSH_BASE_URL") or "").strip().rstrip("/")
        return cls(
            notion_verification_token=(os.getenv("NOTION_WEBHOOK_VERIFICATION_TOKEN") or None),
            linear_secret=(os.getenv("LINEAR_WEBHOOK_SECRET") or None),
            drive_push_base_url=base or None,
        )

# LLM request pacing. The aux (ingest) provider shares the main provider's key
# and endpoint, so background contextualization and a member's live question
# compete for ONE rate limit. Free Gemini is 15 rpm, and a 429 on the answer
# path is a failed answer rather than a slow one.
#
# reserve_rpm is the guarantee: background work never consumes the last N
# requests of a minute, so that many are always there for a person. Interactive
# calls are never throttled — they only report themselves.
#
# Defaults assume the free Gemini tier. Raise max_rpm to match a paid tier;
# set LLM_PACING_ENABLED=false only when the endpoint has no meaningful limit.
DEFAULT_LLM_MAX_RPM = 15
DEFAULT_LLM_RESERVE_RPM = 5
DEFAULT_LLM_PACING_MAX_WAIT_SECONDS = 45.0


@dataclass(frozen=True)
class LLMPacingSettings:
    """How much of the LLM rate limit background work may use."""

    enabled: bool = True
    max_rpm: int = DEFAULT_LLM_MAX_RPM
    reserve_rpm: int = DEFAULT_LLM_RESERVE_RPM
    #: How long one background call may wait for a slot before giving up and
    #: degrading. Bounded because a wedged ingest job is worse than a chunk
    #: without its context prefix.
    max_wait_seconds: float = DEFAULT_LLM_PACING_MAX_WAIT_SECONDS

    @classmethod
    def from_env(cls) -> "LLMPacingSettings":
        max_rpm = int(os.getenv("LLM_MAX_RPM") or DEFAULT_LLM_MAX_RPM)
        reserve = int(os.getenv("LLM_RESERVE_RPM") or DEFAULT_LLM_RESERVE_RPM)
        # A reserve at or above the limit would starve background work
        # completely and silently; clamp so at least one slot remains.
        reserve = max(0, min(reserve, max(0, max_rpm - 1)))
        return cls(
            enabled=env_bool("LLM_PACING_ENABLED", True),
            max_rpm=max_rpm,
            reserve_rpm=reserve,
            max_wait_seconds=float(
                os.getenv("LLM_PACING_MAX_WAIT_SECONDS")
                or DEFAULT_LLM_PACING_MAX_WAIT_SECONDS
            ),
        )


DEFAULT_GRAPH_META_REFRESH_BATCH = 25


@dataclass(frozen=True)
class GraphSettings:
    """Second Brain knowledge-graph settings (git history: docs/plans/2026-09-23-second-brain.md).

    ``meta_refresh_batch`` bounds the metadata-only refresh each ingest job runs
    for documents indexed before people/links were captured (1.1). An unchanged
    document is never re-fetched, so without it those rows would stay invisible
    to the graph forever; the bound is what keeps it from turning one sync into
    a full re-fetch of the corpus. ``0`` switches the refresh off.
    """

    meta_refresh_batch: int = DEFAULT_GRAPH_META_REFRESH_BATCH
    #: Whether ANSWERS use the graph (step 1.6). OFF by default and kept off
    #: until the eval shows a gain (step 1.7): the builder always runs so the
    #: graph fills either way, and this flag only decides whether retrieval
    #: adds the graph's ranked list.
    retrieval_enabled: bool = False
    #: Whether a question may be answered from SEVERAL tools at once when the
    #: graph proves they are connected (``graph/plan.py``). Only meaningful
    #: with ``retrieval_enabled``; on by default there, so one switch turns
    #: the graph on and this one alone can take the cross-tool part back off.
    connected_enabled: bool = True

    @classmethod
    def from_env(cls) -> "GraphSettings":
        raw = os.getenv("GRAPH_META_REFRESH_BATCH")
        try:
            batch = int(raw) if raw not in (None, "") else DEFAULT_GRAPH_META_REFRESH_BATCH
        except ValueError:
            batch = DEFAULT_GRAPH_META_REFRESH_BATCH
        return cls(
            meta_refresh_batch=max(0, batch),
            retrieval_enabled=env_bool("GRAPH_RETRIEVAL_ENABLED", False),
            connected_enabled=env_bool("GRAPH_CONNECTED_ENABLED", True),
        )


@dataclass(frozen=True)
class LiveToolsSettings:
    """The live-tools gateway (git history: docs/plans/2026-09-29-live-connector-access.md).

    Three settings and no more (plan D15): everything else is a constant in
    ``app/livetools/base.py`` until ``live_tool_calls`` shows it needs tuning.
    When enabled, a live read runs inside ordinary Ask: the question classifier
    sets ``needs_live`` (a missing verdict still reads). The old composer
    toggle is gone. Slack, schedulers and eval never set a ``LiveRequest``.

    ``orgs`` empty means every org; otherwise only the listed org ids, for a
    staged rollout.
    """

    enabled: bool = False
    providers: frozenset[str] = frozenset({"linear"})
    orgs: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> "LiveToolsSettings":
        raw_providers = os.getenv("LIVE_TOOLS_PROVIDERS")
        providers = (
            frozenset(p.strip().lower() for p in raw_providers.split(",") if p.strip())
            if raw_providers not in (None, "")
            else frozenset({"linear"})
        )
        raw_orgs = os.getenv("LIVE_TOOLS_ORGS") or ""
        return cls(
            enabled=env_bool("LIVE_TOOLS_ENABLED", False),
            providers=providers,
            orgs=frozenset(o.strip() for o in raw_orgs.split(",") if o.strip()),
        )

    def allows(self, org_id: str | None) -> bool:
        """Switched on, and this org is in the rollout."""
        return self.enabled and bool(org_id) and (not self.orgs or org_id in self.orgs)


@dataclass(frozen=True)
class PersonalMemorySettings:
    """Personal memory, the Second Brain's "who is asking" layer.

    OFF by default like every new layer: unset, no fact is written or read and
    every answer is byte-identical. On, members and org admins can still turn
    it off for themselves / the whole company (``users.memory_enabled``,
    ``organizations.memory_enabled``). ``max_facts`` bounds each person's list:
    past it the oldest UNPINNED fact goes.
    """

    enabled: bool = False
    max_facts: int = 30

    @classmethod
    def from_env(cls) -> "PersonalMemorySettings":
        return cls(
            enabled=env_bool("PERSONAL_MEMORY_ENABLED", False),
            max_facts=_env_positive_int("PERSONAL_MEMORY_MAX_FACTS", 30),
        )
