"""Connected sources, sync, ingest bounds and uploads."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ._env import _oauth_redirect, env_bool

DEFAULT_SOURCE_TYPE = "notion"


@dataclass(frozen=True)
class NotionSettings:
    """Configuration for the Notion content source.

    - ``token``: the default ``NOTION_TOKEN`` integration secret.
    - ``tokens``: per-org secrets from every ``NOTION_TOKEN_<NAME>``; each org's
    own integration, so Notion itself enforces the tenant boundary.
    - ``client_id`` / ``client_secret`` / ``redirect_uri``: the OAuth app.
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
            redirect_uri=_oauth_redirect("NOTION_REDIRECT_URI", "notion"),
        )

    def resolve_token(self, name: str | None = None) -> str:
        """Return the integration secret for ``name`` (or the default token).

        With a ``name``, only that org's token: never a fallback across tenants.
        Raises ``ConfigurationError`` if not configured.
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


@dataclass(frozen=True)
class LinearSettings:
    """Configuration for the Linear content source.

    Two independent paths, no fallback between them: ``token``/``tokens``
    (``LINEAR_TOKEN_<NAME>`` API keys, per org) and the OAuth "Connect Linear"
    app (``client_id``/``client_secret``/``redirect_uri``/``scopes``).
    """

    token: str | None
    tokens: dict[str, str] = field(default_factory=dict)
    client_id: str | None = None
    client_secret: str | None = None
    redirect_uri: str | None = None
    # "read" is Linear's minimal read-only OAuth scope — enough for the GraphQL
    # reads LinearAdapter issues (issues + comments), never write/admin.
    scopes: str = "read"

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
            redirect_uri=_oauth_redirect("LINEAR_REDIRECT_URI", "linear"),
            scopes=os.getenv("LINEAR_OAUTH_SCOPES", cls.scopes),
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


# Reading Forms RESPONSES needs its own scope, opt-in with
# GOOGLE_FORMS_ENABLED=true (then reconnect Google): defaulting it on would
# make every connected tenant reconnect. `drive.readonly` already finds forms.
GOOGLE_FORMS_SCOPE = "https://www.googleapis.com/auth/forms.responses.readonly"


# Expanding a Drive `group:` grant needs the Admin SDK Directory scope and a
# Workspace-admin connection (else 403, failing closed to "no groups").
# Opt-in with GOOGLE_GROUPS_ENABLED=true, then reconnect once.
GOOGLE_GROUPS_SCOPE = "https://www.googleapis.com/auth/admin.directory.group.readonly"


@dataclass(frozen=True)
class GoogleSettings:
    """Configuration for the Google OAuth "Connect" flow (Drive/Docs), OAuth only.

    ``scopes`` is one space-delimited string, Google's own convention.
    """

    client_id: str | None
    client_secret: str | None
    redirect_uri: str | None
    scopes: str = (
        "https://www.googleapis.com/auth/drive.readonly "
        "https://www.googleapis.com/auth/documents.readonly"
    )
    # Bounds on the recursive folder walk: Drive has no "all descendants" query,
    # so it is one files.list per folder, and the Sources change-check runs it too.
    # Ceilings on the Drive folder crawl; hitting one is LOGGED, never silent.
    max_walk_folders: int = 500
    max_documents: int = 2000
    #: Form-response reading (sentiment charts). Off: enabling makes every tenant
    #: reconnect Google.
    forms_enabled: bool = False
    #: Expand a Drive ``group:`` grant into its members for access filtering.
    #: Off: needs an extra scope (every tenant reconnects) and a Workspace-admin
    #: connection. Off, a group-shared file is withheld from all but the
    #: connecting account (fail-closed).
    groups_enabled: bool = False

    @classmethod
    def from_env(cls) -> "GoogleSettings":
        forms_enabled = env_bool("GOOGLE_FORMS_ENABLED", False)
        groups_enabled = env_bool("GOOGLE_GROUPS_ENABLED", False)
        scopes = os.getenv("GOOGLE_OAUTH_SCOPES", cls.scopes)
        # Appended, only when asked, so a GOOGLE_OAUTH_SCOPES override still gets
        # Forms access when the flag is on.
        if forms_enabled and GOOGLE_FORMS_SCOPE not in scopes:
            scopes = f"{scopes} {GOOGLE_FORMS_SCOPE}"
        if groups_enabled and GOOGLE_GROUPS_SCOPE not in scopes:
            scopes = f"{scopes} {GOOGLE_GROUPS_SCOPE}"
        return cls(
            client_id=os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
            redirect_uri=_oauth_redirect("GOOGLE_REDIRECT_URI", "google"),
            scopes=scopes,
            forms_enabled=forms_enabled,
            groups_enabled=groups_enabled,
            max_walk_folders=int(
                os.getenv("GOOGLE_MAX_WALK_FOLDERS") or cls.max_walk_folders
            ),
            max_documents=int(
                os.getenv("GOOGLE_MAX_DOCUMENTS") or cls.max_documents
            ),
        )


@dataclass(frozen=True)
class SlackSettings:
    """Slack: OAuth "Connect" flow plus adapter ingest bounds (OAuth only).

    ``scopes`` is comma-delimited, Slack's own convention.
    """

    client_id: str | None
    client_secret: str | None
    redirect_uri: str | None
    # Bot scopes to list, join and read channels and resolve names.
    # `users:read.email` only matches a member to an EXISTING org account.
    # A newly added scope needs a reconnect (Slack never grants retroactively).
    scopes: str = (
        "channels:history,channels:read,channels:join,"
        "groups:history,groups:read,"
        "im:history,im:read,mpim:history,mpim:read,"
        "users:read,users:read.email,"
        # `chat:write`: the one scope the bot needs to ANSWER (reconnect to get it).
        "chat:write,app_mentions:read,"
        # `files:write`: chart PNGs in replies; without it the numbers come as text.
        "files:write,"
        # Act from Slack: the bot DMs a person the asker named (`conversations.open`).
        "im:write"
    )
    # Ingest bounds: every knob caps something, since a busy channel's full
    # history is not safe to embed unbounded.
    backfill_days: int = 90
    # 15, not 40: "Deploy is frozen till Monday" (28 chars) was dropped from
    # ingest and change detection. 15 still drops "ok", "+1", an emoji.
    min_thread_chars: int = 15
    max_thread_messages: int = 50
    max_documents_per_sync: int = 20000
    # Slack signs every event; unset DISABLES the events route rather than
    # accepting unverified posts.
    signing_secret: str | None = None

    @classmethod
    def from_env(cls) -> "SlackSettings":
        return cls(
            client_id=os.getenv("SLACK_CLIENT_ID"),
            client_secret=os.getenv("SLACK_CLIENT_SECRET"),
            redirect_uri=_oauth_redirect("SLACK_REDIRECT_URI", "slack"),
            scopes=os.getenv("SLACK_BOT_SCOPES", cls.scopes),
            signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
            backfill_days=int(
                os.getenv("SLACK_BACKFILL_DAYS") or cls.backfill_days
            ),
            min_thread_chars=int(
                os.getenv("SLACK_MIN_THREAD_CHARS") or cls.min_thread_chars
            ),
            max_thread_messages=int(
                os.getenv("SLACK_MAX_THREAD_MESSAGES") or cls.max_thread_messages
            ),
            max_documents_per_sync=int(
                os.getenv("SLACK_MAX_DOCUMENTS_PER_SYNC") or cls.max_documents_per_sync
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
class GitHubLiveSettings:
    """Bounds for live GitHub reads."""

    enabled: bool = True
    timeout: float = 10.0
    readme_max_bytes: int = 40_000
    patch_max_bytes: int = 4_000
    max_files_per_commit: int = 25
    max_commits: int = 20
    #: Branches: a prompt full of stale names answers nothing. Pull requests
    #: (below) bound the API spend: reviews cost one call each, ~101 calls per
    #: repo per sync against GitHub's 5,000/hour.
    max_branches: int = 50
    max_pull_requests: int = 100
    # Reviews only for the newest N: a reviewer chart is stable well before 100.
    max_reviewed_pull_requests: int = 30
    max_attempts: int = 3
    #: A GitHub chart first reads the latest pull requests, reviews and commits,
    #: unless read within this many minutes (`GITHUB_CHART_REFRESH_MINUTES`).
    chart_refresh_minutes: int = 10
    #: How long a chart waits for that read (`GITHUB_CHART_REFRESH_WAIT_SECONDS`)
    #: before answering from what was last read; the read finishes behind it.
    chart_refresh_wait_seconds: float = 20.0
    #: Commits per repo for charts (`GITHUB_CHART_MAX_COMMITS`, max 100). Apart
    #: from `max_commits`, which sizes the live Ask prompt.
    chart_max_commits: int = 100

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
            chart_refresh_minutes=max(0, int(os.getenv("GITHUB_CHART_REFRESH_MINUTES", "10"))),
            chart_refresh_wait_seconds=max(0.0, float(
                os.getenv("GITHUB_CHART_REFRESH_WAIT_SECONDS", "20"))),
            chart_max_commits=max(1, min(100, int(os.getenv("GITHUB_CHART_MAX_COMMITS", "100")))),
        )


@dataclass(frozen=True)
class AttachmentSettings:
    """Bounds for an in-chat file attachment; every value is a ceiling.

    ``max_bytes`` protects memory; the rest protect the prompt. A short file is
    injected whole (``inline_char_budget``); a long one as a ``preview_chars``
    head plus a ``read_file`` tool to page through, never silently truncated.
    """

    #: Parsed in memory, so a memory bound too (a 100MB PDF is ~200MB resident).
    max_bytes: int = 10 * 1024 * 1024
    #: What we store; large because paging makes long documents usable.
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
    # Deep analysis lets the model pick more sections of a long upload.
    deep_max_reads: int = 8

    #: Refuse an upload whose extracted text exceeds this many TOKENS (chars bound
    #: the DB, tokens the prompt), told at upload. 0 = no limit; generous on purpose.
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
            deep_max_reads=int(os.getenv("ATTACHMENT_DEEP_MAX_READS") or 8),
            max_tokens=int(os.getenv("ATTACHMENT_MAX_TOKENS") or 120_000),
        )


@dataclass(frozen=True)
class CloudinarySettings:
    """Where an uploaded attachment's bytes and extracted text are stored.

    The object store holds the original and a cached plaintext object (so turns
    do not re-parse); Postgres holds metadata. One backend, so no
    ``base.py``/``factory.py``. Assets are ``authenticated``, never public:
    signed URLs expire, so a forwarded link stops working.
    """

    cloud_name: str
    api_key: str
    api_secret: str
    #: One prefix for everything we write, so a shared bucket stays separable.
    folder: str = "handbook/attachments"
    #: Lifetime of a signed URL. Minutes, not hours: the only consumer is a
    #: download the member just asked for.
    signed_url_ttl_seconds: int = 600

    @property
    def configured(self) -> bool:
        """False when any credential is missing: both or neither, so a half-set store is not "configured"."""
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
class IngestSanitizeSettings:
    """Ingestion-time document size and malformed-content guards."""

    max_document_chars: int = 2_000_000
    max_control_char_ratio: float = 0.05

    @classmethod
    def from_env(cls) -> "IngestSanitizeSettings":
        return cls(
            max_document_chars=int(
                os.getenv("INGEST_MAX_DOCUMENT_CHARS") or cls.max_document_chars
            ),
            max_control_char_ratio=float(
                os.getenv("INGEST_MAX_CONTROL_CHAR_RATIO")
                or cls.max_control_char_ratio
            ),
        )


@dataclass(frozen=True)
class IngestWorkerSettings:
    """Ingestion worker memory admission settings."""

    memory_guard_enabled: bool = True
    max_rss_mb: float = 400.0
    in_api: bool = True  # drain the queue in the API; false = scripts/run_worker.py
    poll_seconds: float = 2.0
    reap_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "IngestWorkerSettings":
        return cls(
            memory_guard_enabled=env_bool(
                "INGEST_MEMORY_GUARD_ENABLED", cls.memory_guard_enabled
            ),
            max_rss_mb=float(os.getenv("INGEST_MAX_RSS_MB") or cls.max_rss_mb),
            in_api=env_bool("INGEST_WORKER_IN_API", True),
            poll_seconds=float(os.getenv("INGEST_WORKER_POLL_SECONDS") or 2.0),
            reap_seconds=float(os.getenv("INGEST_WORKER_REAP_SECONDS") or 60.0),
        )


@dataclass(frozen=True)
class AutoSyncSettings:
    """Configuration for background connection syncing."""

    enabled: bool = True
    # Automatic sync. The interval is a floor: pushing sources sync within a
    # tick, Drive is polled, and a push sent while the box slept is lost. 1h is
    # cheap: unchanged documents are never fetched or embedded, so a no-op sync is
    # one listing. batch_size caps connections per tick (so a big org does not
    # start 40 ingests at once); raise it, not the interval, when schedules slip.
    interval_hours: int = 1
    batch_size: int = 5
    #: Secret for ``POST /internal/tick``. Unset disables it: an open tick spends
    #: every tenant's provider quota.
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
                    or cls.interval_hours
                ),
            ),
            batch_size=max(
                1,
                int(
                    os.getenv("AUTO_SYNC_BATCH_SIZE") or cls.batch_size
                ),
            ),
            tick_secret=(os.getenv("INTERNAL_TICK_SECRET") or None),
        )


@dataclass(frozen=True)
class WebhookSettings:
    """Push receivers for Notion, Linear and Drive (`app/api/webhooks.py`).

    An unset field closes that receiver (404) and the provider is polled on the
    auto-sync interval.
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
