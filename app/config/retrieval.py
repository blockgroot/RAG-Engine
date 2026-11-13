"""Storage, retrieval, answering and per-agent settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace

from ._env import _env_positive_int, env_bool
from .models import DEFAULT_EMBEDDING_DIM

DEFAULT_MAX_CHUNK_CHARS = 4000

DEFAULT_RETRIEVAL_RRF_K = 60

AUDIT_BACKENDS = ("llm", "lettuce")


@dataclass(frozen=True)
class DatabaseSettings:
    """Postgres and pgvector configuration."""

    url: str | None
    embedding_dim: int = DEFAULT_EMBEDDING_DIM
    pool_min_size: int = 1
    pool_max_size: int = 10
    # Keyword matches BM25 scores per query. With any-word matching, 200 kept the
    # most right documents in the candidates (94% vs 92% at 500, Benchmark 2) and is faster.
    keyword_candidate_limit: int = 200

    @classmethod
    def from_env(cls) -> "DatabaseSettings":
        return cls(
            url=os.getenv("DATABASE_URL"),
            embedding_dim=int(os.getenv("EMBEDDING_DIM") or cls.embedding_dim),
            pool_min_size=int(os.getenv("DB_POOL_MIN_SIZE") or cls.pool_min_size),
            pool_max_size=int(os.getenv("DB_POOL_MAX_SIZE") or cls.pool_max_size),
            keyword_candidate_limit=_env_positive_int(
                "KEYWORD_CANDIDATE_LIMIT", cls.keyword_candidate_limit
            ),
        )


@dataclass(frozen=True)
class ChunkingSettings:
    """Configuration for document chunking (sizes measured in tokens)."""

    chunk_size: int = 256
    chunk_overlap: int = 40
    token_backend: str = "heuristic"
    max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS

    @classmethod
    def from_env(cls) -> "ChunkingSettings":
        return cls(
            chunk_size=int(os.getenv("CHUNK_SIZE") or cls.chunk_size),
            chunk_overlap=int(os.getenv("CHUNK_OVERLAP") or cls.chunk_overlap),
            max_chunk_chars=int(
                os.getenv("CHUNK_MAX_CHARS") or cls.max_chunk_chars
            ),
            token_backend=(
                os.getenv("CHUNK_TOKEN_BACKEND") or cls.token_backend
            ).strip().lower(),
        )


@dataclass(frozen=True)
class VectorStoreSettings:
    """Configuration for the vector store abstraction layer."""

    backend: str = "pgvector"

    @classmethod
    def from_env(cls) -> "VectorStoreSettings":
        return cls(
            backend=(os.getenv("VECTOR_STORE_BACKEND") or cls.backend).lower(),
        )


@dataclass(frozen=True)
class RagSettings:
    """RAG retrieval and generation settings."""

    top_k: int = 5
    similarity_threshold: float = 0.35
    fallback_response: str = (
        "I don't have information on that in the available policy documents."
    )
    max_context_chars: int = 6000
    max_answer_tokens: int | None = 700
    # A question pinned to a TAG (one Slack channel) is read whole when both
    # bounds hold, else ranked retrieval runs, so the read is never silently
    # partial. 25 threads were ~38 chunks / ~27k chars: ~2x headroom.
    scope_whole_max_chunks: int = 120
    scope_whole_max_chars: int = 60000
    # Chunks either side of the best hits added to their block (0 = off): the
    # right document's next paragraph was often the missing detail.
    neighbor_chunks: int = 0
    # 0 = neighbours for the top 2 hits. N > 0 = for the best hit of each of the
    # top N different documents (read nearly whole); raise RAG_MAX_CONTEXT_CHARS with it.
    neighbor_top_docs: int = 0
    # Tell the model not to attach one document's facts to another's subject
    # (Benchmark 1: wrong-document facts were the second-largest loss).
    focus_rule: bool = False
    # Wide reads: when several documents score near the best, read up to
    # ``wide_max_hits`` passages, at most ``wide_per_doc`` each, so the room goes
    # to OTHER documents. Driven by reranker scores, never wording. Off unless
    # ``wide_max_hits`` > ``top_k`` and ``wide_doc_ratio`` > 0.
    wide_max_hits: int = 0
    wide_doc_ratio: float = 0.0
    wide_per_doc: int = 1
    # Once wide, add documents down to this share of the best score (0 = ``wide_doc_ratio``).
    wide_include_ratio: float = 0.0
    # Read wide even when only one document is strong (Deep's extra effort).
    wide_always: bool = False
    # 0 = scale ``max_context_chars`` by wide_max_hits / top_k.
    wide_max_context_chars: int = 0
    # Answer the part CONTEXT covers and say what is missing, instead of the
    # fallback (Benchmark 2: 6 refusals had the right document in the prompt).
    partial_rule: bool = False
    # When blocks disagree, prefer the newer or superseding one and say so.
    conflict_rule: bool = False
    # A company-wide question answered from pieces spread over several blocks.
    assemble_rule: bool = False
    # Chat's "Deep analysis": a deeper read, no extra AI call; per request, so
    # normal Ask keeps its cost.
    deep_top_docs: int = 3
    deep_neighbor_chunks: int = 8
    deep_max_context_chars: int = 30000
    # Deep's extra effort only: passages it may take when the evidence is spread
    # over many documents (a company-wide question).
    deep_wide_max_hits: int = 20
    # Deep's answer may run longer than normal Ask's cap: a complete list of 30-40
    # items does not fit 700 tokens, and a cut-off list reads as the whole list.
    deep_max_answer_tokens: int = 2000
    # Extra effort reads wide on every question, down to this x the best score:
    # one strong match otherwise kept broad questions narrow.
    deep_wide_include_ratio: float = 0.05

    def deep(self, *, extra: bool = False) -> "RagSettings":
        """These settings with the deep read on, one budget for ranked and wide reads.

        Deep keeps the normal pool (a bigger one found no more right documents).
        With ``extra`` a spread question may take ``deep_wide_max_hits`` passages,
        one per document first (`retrieval._spread`), and answer up to
        ``deep_max_answer_tokens``.
        """
        if extra:
            self = replace(
                self,
                wide_max_hits=max(self.wide_max_hits, self.deep_wide_max_hits),
                wide_always=True,
                wide_include_ratio=self.deep_wide_include_ratio,
                max_answer_tokens=(None if self.max_answer_tokens is None
                                   else max(self.max_answer_tokens, self.deep_max_answer_tokens)),
            )
        return replace(
            self,
            neighbor_top_docs=self.deep_top_docs,
            neighbor_chunks=self.deep_neighbor_chunks,
            max_context_chars=self.deep_max_context_chars,
            wide_max_context_chars=self.deep_max_context_chars,
        )

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
            max_answer_tokens: int | None = cls.max_answer_tokens
        elif raw_answer_tokens.strip() in ("", "0"):
            max_answer_tokens = None
        else:
            max_answer_tokens = int(raw_answer_tokens)
        return cls(
            top_k=int(os.getenv("RAG_TOP_K") or cls.top_k),
            scope_whole_max_chunks=int(
                os.getenv("RAG_SCOPE_WHOLE_MAX_CHUNKS") or cls.scope_whole_max_chunks
            ),
            scope_whole_max_chars=int(
                os.getenv("RAG_SCOPE_WHOLE_MAX_CHARS") or cls.scope_whole_max_chars
            ),
            similarity_threshold=float(
                os.getenv("RAG_SIMILARITY_THRESHOLD") or cls.similarity_threshold
            ),
            fallback_response=os.getenv("RAG_FALLBACK_RESPONSE") or cls.fallback_response,
            max_context_chars=int(
                os.getenv("RAG_MAX_CONTEXT_CHARS") or cls.max_context_chars
            ),
            max_answer_tokens=max_answer_tokens,
            # Env defaults are the benchmarked version; dataclass defaults stay off so a
            # bare RagSettings() in a test is the plain pipeline.
            neighbor_chunks=int(os.getenv("RAG_NEIGHBOR_CHUNKS") or 1),
            neighbor_top_docs=int(os.getenv("RAG_NEIGHBOR_TOP_DOCS") or 0),
            focus_rule=env_bool("RAG_FOCUS_RULE", True),
            wide_max_hits=int(os.getenv("RAG_WIDE_MAX_HITS") or 10),
            wide_doc_ratio=float(os.getenv("RAG_WIDE_DOC_RATIO") or 0.3),
            wide_per_doc=int(os.getenv("RAG_WIDE_PER_DOC") or 1),
            wide_include_ratio=float(os.getenv("RAG_WIDE_INCLUDE_RATIO") or 0.15),
            wide_max_context_chars=int(os.getenv("RAG_WIDE_MAX_CONTEXT_CHARS") or 0),
            partial_rule=env_bool("RAG_PARTIAL_RULE", True),
            conflict_rule=env_bool("RAG_CONFLICT_RULE", True),
            assemble_rule=env_bool("RAG_ASSEMBLE_RULE", True),
            deep_top_docs=int(os.getenv("RAG_DEEP_TOP_DOCS") or 3),
            deep_neighbor_chunks=int(os.getenv("RAG_DEEP_NEIGHBOR_CHUNKS") or 8),
            deep_max_context_chars=int(os.getenv("RAG_DEEP_MAX_CONTEXT_CHARS") or 30000),
            deep_wide_max_hits=int(os.getenv("RAG_DEEP_WIDE_MAX_HITS") or 20),
            deep_max_answer_tokens=int(os.getenv("RAG_DEEP_MAX_ANSWER_TOKENS") or 2000),
            deep_wide_include_ratio=float(os.getenv("RAG_DEEP_WIDE_INCLUDE_RATIO") or 0.05),
        )


@dataclass(frozen=True)
class WorkspaceAgentSettings:
    """Workspace-agent-specific settings."""

    fallback_response: str = (
        "I don't have anything about that in this workspace's connected content."
    )

    @classmethod
    def from_env(cls) -> "WorkspaceAgentSettings":
        return cls(
            fallback_response=os.getenv("WORKSPACE_FALLBACK_RESPONSE")
            or cls.fallback_response,
        )


@dataclass(frozen=True)
class SlackAgentSettings:
    """Slack-agent-specific settings."""

    fallback_response: str = (
        "I couldn't find that in the connected Slack channels. It may have been "
        "discussed in a channel that isn't connected, or before the synced history "
        "window."
    )

    @classmethod
    def from_env(cls) -> "SlackAgentSettings":
        return cls(
            fallback_response=os.getenv("SLACK_FALLBACK_RESPONSE")
            or cls.fallback_response,
        )


@dataclass(frozen=True)
class LinearAgentSettings:
    """Linear-agent-specific settings."""

    fallback_response: str = (
        "I couldn't find that in the connected Linear issues. It may not have been "
        "ingested yet, or no issue matches this question."
    )

    @classmethod
    def from_env(cls) -> "LinearAgentSettings":
        return cls(
            fallback_response=os.getenv("LINEAR_FALLBACK_RESPONSE")
            or cls.fallback_response,
        )


@dataclass(frozen=True)
class NotionAgentSettings:
    """Notion-agent-specific settings."""

    fallback_response: str = (
        "I couldn't find that in the connected Notion pages. It may not have been "
        "shared with the integration, or hasn't been ingested yet."
    )

    @classmethod
    def from_env(cls) -> "NotionAgentSettings":
        return cls(
            fallback_response=os.getenv("NOTION_AGENT_FALLBACK_RESPONSE")
            or cls.fallback_response,
        )


@dataclass(frozen=True)
class DriveAgentSettings:
    """Drive-agent-specific settings."""

    fallback_response: str = (
        "I couldn't find that in the connected Google Drive documents. It may not "
        "be in the synced folder, or hasn't been ingested yet."
    )

    @classmethod
    def from_env(cls) -> "DriveAgentSettings":
        return cls(
            fallback_response=os.getenv("DRIVE_AGENT_FALLBACK_RESPONSE")
            or cls.fallback_response,
        )


@dataclass(frozen=True)
class GitHubAgentSettings:
    """GitHub-agent-specific settings."""

    fallback_response: str = (
        "I couldn't find that in the connected GitHub repositories. Try naming the "
        "repository, or check that it's included in this organization's GitHub "
        "installation."
    )
    evidence_recovery_enabled: bool = True
    recovery_commit_count: int = 10

    @classmethod
    def from_env(cls) -> "GitHubAgentSettings":
        return cls(
            fallback_response=os.getenv("GITHUB_FALLBACK_RESPONSE")
            or cls.fallback_response,
            evidence_recovery_enabled=os.getenv(
                "GITHUB_EVIDENCE_RECOVERY_ENABLED", "true"
            ).lower()
            != "false",
            recovery_commit_count=int(os.getenv("GITHUB_RECOVERY_COMMIT_COUNT", "10")),
        )


@dataclass(frozen=True)
class MemorySettings:
    """Conversation-memory sizing."""

    recent_turns: int = 3
    fold_wait_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "MemorySettings":
        return cls(
            recent_turns=int(os.getenv("MEMORY_RECENT_TURNS") or cls.recent_turns),
            fold_wait_seconds=float(
                os.getenv("MEMORY_FOLD_WAIT_SECONDS") or cls.fold_wait_seconds
            ),
        )


@dataclass(frozen=True)
class ReuseSettings:
    """Retrieval-reuse settings for conversation follow-ups."""

    enabled: bool = True
    threshold: float = 0.72

    @classmethod
    def from_env(cls) -> "ReuseSettings":
        return cls(
            enabled=env_bool("RETRIEVAL_REUSE_ENABLED", cls.enabled),
            threshold=float(
                os.getenv("RETRIEVAL_REUSE_THRESHOLD") or cls.threshold
            ),
        )


@dataclass(frozen=True)
class ToneSettings:
    """Question-tone classification settings."""

    enabled: bool = True

    @classmethod
    def from_env(cls) -> "ToneSettings":
        return cls(
            enabled=env_bool("TONE_CLASSIFY_ENABLED", cls.enabled),
        )


@dataclass(frozen=True)
class RecoverySettings:
    """Bounded retrieval-recovery settings."""

    enabled: bool = True
    max_queries: int = 2
    # Search with the alternative wordings up front too: a differently worded
    # document can be missed while the search still looks "good enough".
    proactive: bool = False

    @classmethod
    def from_env(cls) -> "RecoverySettings":
        return cls(
            enabled=env_bool("RECOVERY_ENABLED", cls.enabled),
            max_queries=int(os.getenv("RECOVERY_MAX_QUERIES") or cls.max_queries),
            proactive=env_bool("RECOVERY_PROACTIVE", False),
        )


@dataclass(frozen=True)
class DocTablesSettings:
    """Charts from figures inside documents (``app/doctables``).

    Tables are always kept (no AI). Figures in sentences need the model:

    - ``text_enabled``: `DOCTABLES_TEXT_ENABLED`, read prose figures inline at
    ingest, paced on the background budget. On by default; off, only Chart
    mode reads prose.
    - ``text_max_chars``: how much of a document the model sees; the chart says
    when it was cut.
    - ``backfill_batch``: indexed documents re-read for tables per sync (0 =
    off), since a sync skips unchanged ones. No re-embedding.
    - ``answers_enabled``: `DOCTABLES_ANSWERS_ENABLED`, Ask works out a figure
    over a sheet's rows with a checked query (``insights/table_answers.py``).
    Off by default.
    """

    text_enabled: bool = True
    text_max_chars: int = 12000
    backfill_batch: int = 20
    answers_enabled: bool = False

    @classmethod
    def from_env(cls) -> "DocTablesSettings":
        return cls(
            text_enabled=env_bool("DOCTABLES_TEXT_ENABLED", True),
            text_max_chars=max(1000, int(os.getenv("DOCTABLES_TEXT_MAX_CHARS") or 12000)),
            backfill_batch=max(0, int(os.getenv("DOCTABLES_BACKFILL_BATCH") or 20)),
            answers_enabled=env_bool("DOCTABLES_ANSWERS_ENABLED", False),
        )


@dataclass(frozen=True)
class AuditSettings:
    """Post-generation groundedness audit.

    A bounded second call re-checks a drafted answer against the same context;
    it can only downgrade to the fallback, and an audit failure keeps the
    answer. Off by default (extra cost per answer).

    - ``backend``: ``llm`` (default) or ``lettuce``, a LettuceDetect span
    classifier on an endpoint WE run (it receives tenant chunks).
    - ``lettuce_threshold``: a flagged span at or above this downgrades.
    """

    enabled: bool = False
    backend: str = "llm"
    lettuce_url: str | None = None
    lettuce_token: str | None = None
    # Best balanced-accuracy cutoff measured on 60 RAGTruth-QA examples
    # (scripts/bench_answer_check.py) was 0.61; re-measure on your own labels.
    lettuce_threshold: float = 0.6
    lettuce_timeout: float = 8.0

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
            enabled=env_bool("RAG_AUDIT_ENABLED", cls.enabled),
            backend=backend,
            lettuce_url=url,
            lettuce_token=os.getenv("RAG_AUDIT_LETTUCE_TOKEN") or None,
            lettuce_threshold=float(
                os.getenv("RAG_AUDIT_LETTUCE_THRESHOLD") or cls.lettuce_threshold
            ),
            lettuce_timeout=float(
                os.getenv("RAG_AUDIT_LETTUCE_TIMEOUT") or cls.lettuce_timeout
            ),
        )


@dataclass(frozen=True)
class QueryNormSettings:
    """Corpus-vocabulary spelling correction before retrieval."""

    enabled: bool = True
    max_edit_distance: int = 1
    min_word_length: int = 4
    cache_max_orgs: int = 50

    @classmethod
    def from_env(cls) -> "QueryNormSettings":
        return cls(
            enabled=env_bool("QUERY_NORM_ENABLED", cls.enabled),
            max_edit_distance=int(
                os.getenv("QUERY_NORM_MAX_EDIT_DISTANCE")
                or cls.max_edit_distance
            ),
            min_word_length=int(
                os.getenv("QUERY_NORM_MIN_WORD_LENGTH")
                or cls.min_word_length
            ),
            cache_max_orgs=_env_positive_int(
                "QUERY_NORM_CACHE_MAX_ORGS", cls.cache_max_orgs
            ),
        )


@dataclass(frozen=True)
class WebSearchSettings:
    """Web-search tool settings."""

    enabled: bool = True
    provider: str = "duckduckgo"
    api_key: str | None = None
    max_results: int = 5
    timeout: float = 8.0

    @classmethod
    def from_env(cls) -> "WebSearchSettings":
        return cls(
            enabled=env_bool("WEB_SEARCH_ENABLED", cls.enabled),
            provider=(os.getenv("WEB_SEARCH_PROVIDER") or cls.provider).lower(),
            api_key=os.getenv("WEB_SEARCH_API_KEY"),
            max_results=int(os.getenv("WEB_SEARCH_MAX_RESULTS") or cls.max_results),
            timeout=float(os.getenv("WEB_SEARCH_TIMEOUT") or cls.timeout),
        )


@dataclass(frozen=True)
class RetrievalSettings:
    """Query-time retrieval settings."""

    hybrid_enabled: bool = True
    rerank_enabled: bool = True
    candidate_pool: int = 40
    rrf_k: int = DEFAULT_RETRIEVAL_RRF_K
    # Drop a reranked hit below this fraction of the best score (0 = off).
    # Uncalibrated, so off until logged ``rerank_scores`` show the split.
    rerank_min_ratio: float = 0.0
    # Show the reranker each passage's title, so a name only in the title counts.
    # Reranker input only; the prompt and gate are unchanged.
    rerank_with_title: bool = False

    @classmethod
    def from_env(cls) -> "RetrievalSettings":
        return cls(
            hybrid_enabled=env_bool("RETRIEVAL_HYBRID_ENABLED", cls.hybrid_enabled),
            rerank_enabled=env_bool("RETRIEVAL_RERANK_ENABLED", cls.rerank_enabled),
            candidate_pool=int(
                os.getenv("RETRIEVAL_CANDIDATE_POOL") or cls.candidate_pool
            ),
            rrf_k=int(os.getenv("RETRIEVAL_RRF_K") or cls.rrf_k),
            rerank_min_ratio=float(os.getenv("RETRIEVAL_RERANK_MIN_RATIO") or 0.2),
            rerank_with_title=env_bool("RETRIEVAL_RERANK_WITH_TITLE", False),
        )


@dataclass(frozen=True)
class GraphSettings:
    """Knowledge-graph settings.

    ``meta_refresh_batch`` bounds the metadata-only refresh per ingest for
    documents indexed before people/links were captured (``0`` = off).
    """

    meta_refresh_batch: int = 25
    #: Whether ANSWERS use the graph. Off by default; the builder runs either way,
    #: this only adds the graph's ranked list to retrieval.
    retrieval_enabled: bool = False
    #: Whether a question may be answered from SEVERAL tools the graph proves
    #: connected (``graph/plan.py``). Needs ``retrieval_enabled``.
    connected_enabled: bool = True

    @classmethod
    def from_env(cls) -> "GraphSettings":
        raw = os.getenv("GRAPH_META_REFRESH_BATCH")
        try:
            batch = int(raw) if raw not in (None, "") else cls.meta_refresh_batch
        except ValueError:
            batch = cls.meta_refresh_batch
        return cls(
            meta_refresh_batch=max(0, batch),
            retrieval_enabled=env_bool("GRAPH_RETRIEVAL_ENABLED", False),
            connected_enabled=env_bool("GRAPH_CONNECTED_ENABLED", True),
        )


@dataclass(frozen=True)
class LiveToolsSettings:
    """The live-tools gateway.

    Other knobs are constants in ``app/livetools/base.py``. A live read runs
    inside Ask when the question check sets ``needs_live``; Slack, schedulers and
    eval never set a ``LiveRequest``. Empty ``orgs`` means every org.
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
    """Personal memory: the "who is asking" layer.

    Off by default (nothing written or read). On, people and admins can still
    turn it off (``users``/``organizations.memory_enabled``). ``max_facts``:
    past it the oldest unpinned fact goes.
    """

    enabled: bool = False
    max_facts: int = 30

    @classmethod
    def from_env(cls) -> "PersonalMemorySettings":
        return cls(
            enabled=env_bool("PERSONAL_MEMORY_ENABLED", False),
            max_facts=_env_positive_int("PERSONAL_MEMORY_MAX_FACTS", 30),
        )


@dataclass(frozen=True)
class ChartRenderSettings:
    """Size of a chart drawn as a picture (Slack): a 2x image of a ~600px preview, rendered in under a second."""

    width: int = 1200
    height: int = 700
    dpi: int = 144

    @classmethod
    def from_env(cls) -> "ChartRenderSettings":
        return cls(
            width=_env_positive_int("CHART_RENDER_WIDTH", 1200),
            height=_env_positive_int("CHART_RENDER_HEIGHT", 700),
            dpi=_env_positive_int("CHART_RENDER_DPI", 144),
        )
