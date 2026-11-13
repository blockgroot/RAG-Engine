"""The API server, email, updates, schedulers and Agent mode."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ._env import env_bool


@dataclass(frozen=True)
class UpdateSettings:
    """Which version this copy runs, and how it hears about newer ones.

    - ``deployment``: ``cloud`` (we update; admins told) or ``selfhosted``
    (admins offered the update).
    - ``current_version``: ``HANDBOOK_VERSION``, else the image's
    ``HANDBOOK_IMAGE_VERSION``; neither means nothing is checked.
    - ``build_commit``: ``RENDER_GIT_COMMIT``, shown, never compared with releases.
    - ``check_enabled``: off for a server that must contact no one.
    - ``updater_url``/``updater_token``: the self-hosted updater; both or
    neither (else the page shows the upgrade command).
    """

    deployment: str = "cloud"
    current_version: str | None = None
    build_commit: str | None = None
    #: Every install reads the same public list of releases: the main repo and
    #: the images can stay private while the version check needs no credentials.
    feed_url: str = (
        "https://raw.githubusercontent.com/blockgroot/handbook-releases/main/releases.json"
    )
    check_enabled: bool = True
    updater_url: str | None = None
    updater_token: str | None = None

    @property
    def updater_configured(self) -> bool:
        return bool(self.updater_url and self.updater_token)

    @classmethod
    def from_env(cls) -> "UpdateSettings":
        return cls(
            deployment=(os.getenv("HANDBOOK_DEPLOYMENT") or "cloud").strip().lower(),
            current_version=(
                os.getenv("HANDBOOK_VERSION") or os.getenv("HANDBOOK_IMAGE_VERSION") or ""
            ).strip().lstrip("v") or None,
            build_commit=(os.getenv("RENDER_GIT_COMMIT") or "").strip() or None,
            feed_url=os.getenv("UPDATE_FEED_URL") or cls.feed_url,
            check_enabled=env_bool("UPDATE_CHECK_ENABLED", True),
            updater_url=os.getenv("UPDATER_URL") or None,
            updater_token=os.getenv("UPDATER_TOKEN") or None,
        )


@dataclass(frozen=True)
class ApiSettings:
    """HTTP API server configuration.

    - ``cors_origins``: exact frontend origins allowed with credentials; empty means reject.
    - ``frontend_url``: base for magic links and post-connect redirects.
    """

    cors_origins: list[str] = field(default_factory=list)
    host: str = "0.0.0.0"
    port: int = 8000
    frontend_url: str | None = None
    model_warmup: bool = True  # off on low-RAM machines

    @classmethod
    def from_env(cls) -> "ApiSettings":
        raw_origins = os.getenv("API_CORS_ORIGINS") or ""
        origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
        return cls(
            cors_origins=origins,
            host=os.getenv("API_HOST") or cls.host,
            port=int(os.getenv("API_PORT") or cls.port),
            frontend_url=os.getenv("FRONTEND_URL"),
            model_warmup=env_bool("MODEL_WARMUP_ON_STARTUP", True),
        )


@dataclass(frozen=True)
class EmailSettings:
    """Outbound email configuration.

    - ``sender``: ``console`` (default), ``smtp``, ``resend`` or ``sendgrid``
    (HTTPS ones work where SMTP ports are blocked).
    - ``smtp_*``: for ``smtp``; ``smtp_from`` is also the From for Resend/SendGrid.
    - ``resend_api_key``: its sandbox sender reaches only the account's own address.
    - ``sendgrid_api_key``: a verified Single Sender reaches anyone, no domain.
    - ``owner_notification_email``: where signup requests go; the only review surface.
    """

    sender: str = "console"
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
            sender=(os.getenv("EMAIL_SENDER") or cls.sender).lower(),
            smtp_host=os.getenv("EMAIL_SMTP_HOST"),
            smtp_port=int(smtp_port) if smtp_port else None,
            smtp_username=os.getenv("EMAIL_SMTP_USERNAME"),
            smtp_password=os.getenv("EMAIL_SMTP_PASSWORD"),
            smtp_from=os.getenv("EMAIL_SMTP_FROM"),
            resend_api_key=os.getenv("EMAIL_RESEND_API_KEY"),
            sendgrid_api_key=os.getenv("EMAIL_SENDGRID_API_KEY"),
            owner_notification_email=os.getenv("OWNER_NOTIFICATION_EMAIL"),
        )


@dataclass(frozen=True)
class SchedulerSettings:
    """Configuration for the recurring activity-report scheduler."""

    enabled: bool = True
    # Activity scheduler: a slow poll (schedulers are weekly/monthly); batch_size
    # keeps a burst from monopolising the worker thread.
    poll_seconds: int = 300
    max_attempts: int = 3
    batch_size: int = 5

    @classmethod
    def from_env(cls) -> "SchedulerSettings":
        return cls(
            enabled=(os.getenv("SCHEDULER_ENABLED", "true").strip().lower()
                     not in {"false", "0", "no"}),
            poll_seconds=int(
                os.getenv("SCHEDULER_POLL_SECONDS") or cls.poll_seconds
            ),
            max_attempts=int(
                os.getenv("SCHEDULER_MAX_ATTEMPTS") or cls.max_attempts
            ),
            batch_size=int(
                os.getenv("SCHEDULER_BATCH_SIZE") or cls.batch_size
            ),
        )


@dataclass(frozen=True)
class ActionsSettings:
    """Agent mode: writes into tools through MCP.

    Whether a company uses it is its admins' ``act`` row (``policy.act_on``),
    not here. ``ttl_seconds``: an approval card's life (no stale writes from an
    old tab). ``rate_per_hour``: executed writes per person. ``block_wider``:
    refuse rather than warn on a wider or unknown audience.
    """

    ttl_seconds: int = 900
    rate_per_hour: int = 100
    block_wider: bool = False
    # Where a person's MCP sign-in returns: the browser-facing /api proxy, so it
    # survives a backend move.
    mcp_redirect_uri: str | None = None
    mcp_timeout_seconds: float = 20.0
    #: Lookup calls (admin-marked read tools) one request may make before drafting.
    mcp_max_lookups: int = 8
    #: Tools one drafting call is offered, each connector's nearest first
    #: (``service._fit_tools``). Never above 128, which models refuse.
    mcp_max_tools: int = 40
    #: Where Handbook's own MCP servers answer (``app/mcphost``: Slack, Google),
    #: through the same /api proxy; ``HANDBOOK_MCP_URL`` when they move out.
    mcp_host_url: str | None = None

    @classmethod
    def from_env(cls) -> "ActionsSettings":
        return cls(
            ttl_seconds=max(60, int(os.getenv("ACTION_TTL_SECONDS", "900"))),
            rate_per_hour=max(1, int(os.getenv("ACTION_RATE_PER_HOUR", "100"))),
            block_wider=env_bool("ACTIONS_BLOCK_WIDER", False),
            mcp_redirect_uri=os.getenv("MCP_REDIRECT_URI") or (
                f"{os.getenv('FRONTEND_URL').rstrip('/')}/api/act/mcp/callback"
                if os.getenv("FRONTEND_URL") else None),
            mcp_timeout_seconds=float(os.getenv("MCP_TIMEOUT_SECONDS", "20")),
            mcp_max_lookups=max(0, int(os.getenv("MCP_MAX_LOOKUPS", "8"))),
            mcp_max_tools=min(128, max(1, int(os.getenv("MCP_MAX_TOOLS", "40")))),
            mcp_host_url=(os.getenv("HANDBOOK_MCP_URL") or (
                f"{os.getenv('FRONTEND_URL').rstrip('/')}/api/mcp" if os.getenv("FRONTEND_URL") else None)),
        )
