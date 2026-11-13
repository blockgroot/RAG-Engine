"""Guards, privacy, rate limits, AI spend and auth."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from ._env import _env_positive_int, env_bool
from .models import DEFAULT_GROQ_BASE_URL, GroqSettings

# Tokens per minute per answering model, each model its own buckets:
# ``model=person_in:person_out:company_in:company_out``. Sized so a person can
# ask ~10 questions a minute. An unlisted model has no token limit.
DEFAULT_AI_TOKEN_LIMITS = (
    "claude-sonnet-5-5=60000:8000:400000:40000,"
    "claude-haiku-5-5=60000:8000:400000:40000"
)

# Dollars per million input / output tokens by model id (dated or prefixed ids
# match their base). An unlisted model is not charged.
DEFAULT_AI_PRICES = (
    "claude-sonnet-5-5=2:10,claude-haiku-5-5=0.10:0.50"
)

#: Hosts an answer may link to without the link being in its sources (the
#: connected tools' domains). Query strings are still cut (`security/links.py`).
DEFAULT_LINK_ALLOWLIST = (
    "notion.so", "notion.site", "docs.google.com", "drive.google.com",
    "slack.com", "linear.app", "github.com",
)


@dataclass(frozen=True)
class SecuritySettings:
    """Deterministic prompt-injection controls.

    ``link_allowlist``: `SECURITY_LINK_ALLOWLIST`, comma-separated hosts; empty
    means only links verbatim in the sources.
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


DEFAULT_SAFEGUARD_MODEL = "openai/gpt-oss-safeguard-20b"


@dataclass(frozen=True)
class GuardSettings:
    """Injection scoring of untrusted text (``app/guard``).

    - ``backend``: ``prompt_guard`` (Llama Prompt Guard 2 86M; catches override
    wording; 22M missed non-English) or ``safeguard`` (gpt-oss-safeguard-20b
    with our policy; catches action injections). Each chunk records its
    scorer, so switching rescans via the backfill.
    - ``mode``: ``off`` (default, no calls), ``shadow`` (score and log only) or
    ``enforce`` (drop flagged passages).
    - ``threshold``: at or above is flagged; the raw score is stored, so no
    rescan on change. 0.9: a real IT page scored 0.70, planted overrides 0.999.
    - ``backfill_batch``: chunks scored per tick where ingest left NULL (a
    rate-limited sync is scored later, never failed).
    - ``answer_check``: `GUARD_ANSWER_CHECK`, also moderate the finished answer
    (`guard/moderation.py`); off, one call per answer.
    - ``api_key``/``base_url``: the guard's OWN account, so it spends no other
    quota. Both or neither (neither = Groq's key).
    - ``safeguard_model``: the safeguard's id on that endpoint. Prompt Guard 2
    is Groq-only, so another endpoint uses ``GUARD_BACKEND=safeguard``.
    """

    mode: str = "off"
    backend: str = "prompt_guard"
    model: str = "meta-llama/llama-prompt-guard-2-86m"
    threshold: float = 0.9
    timeout: float = 5.0
    backfill_batch: int = 40
    answer_check: bool = False
    api_key: str | None = None
    base_url: str = DEFAULT_GROQ_BASE_URL
    safeguard_model: str = DEFAULT_SAFEGUARD_MODEL

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
        own_key = os.getenv("GUARD_API_KEY") or None
        own_url = os.getenv("GUARD_BASE_URL") or None
        if bool(own_key) != bool(own_url):
            raise ConfigurationError("GUARD_API_KEY and GUARD_BASE_URL are both set or neither")
        if own_key:
            api_key, base_url = own_key, own_url
        else:
            groq = GroqSettings.from_env()
            api_key, base_url = groq.api_key, groq.base_url
        return cls(
            mode=mode,
            backend=backend,
            model=os.getenv("GUARD_MODEL") or cls.model,
            threshold=float(os.getenv("GUARD_THRESHOLD") or 0.9),
            timeout=float(os.getenv("GUARD_TIMEOUT") or 5.0),
            backfill_batch=int(os.getenv("GUARD_BACKFILL_BATCH") or 40),
            answer_check=env_bool("GUARD_ANSWER_CHECK", False),
            api_key=api_key,
            base_url=base_url,
            safeguard_model=os.getenv("GUARD_SAFEGUARD_MODEL") or cls.safeguard_model,
        )


@dataclass(frozen=True)
class PrivacySettings:
    """Mask personal identifiers before text leaves for an outside service (``app/privacy``).

    On by default for every model, the remote embedder and reranker, and the
    guard. ``MASK_IDENTIFIERS=false`` is the kill switch.
    """

    mask_identifiers: bool = True

    @classmethod
    def from_env(cls) -> "PrivacySettings":
        return cls(mask_identifiers=env_bool("MASK_IDENTIFIERS", True))


@dataclass(frozen=True)
class RequestBudgetSettings:
    """Global per-request deadline for ``RagPipeline.answer()``."""

    deadline_seconds: float = 45.0
    min_stage_seconds: float = 3.0

    @classmethod
    def from_env(cls) -> "RequestBudgetSettings":
        return cls(
            deadline_seconds=float(
                os.getenv("REQUEST_DEADLINE_SECONDS") or cls.deadline_seconds
            ),
            min_stage_seconds=float(
                os.getenv("REQUEST_BUDGET_MIN_STAGE_SECONDS") or cls.min_stage_seconds
            ),
        )


@dataclass(frozen=True)
class RateLimitSettings:
    """Token-bucket rate limits (``app/ratelimit``) and the answers-at-once cap.

    Empty ``redis_url`` keeps buckets in process memory: fine for one process,
    wrong for several (each allows the full limit).
    """

    enabled: bool = True
    redis_url: str | None = None
    # Token buckets: BURST at once, refilled at PER_MINUTE. A question (chat,
    # Agent, charts, scheduler setup, Slack) costs the model.
    questions_per_minute: float = 10.0
    question_burst: int = 5
    # The company's bucket is sized by its members, so one large company cannot
    # use the AI account every company shares, and a small one is not starved.
    org_questions_per_member: float = 3.0
    org_questions_min: int = 30
    # Anything that sends an email, per IP or person: room for an office signing
    # in together, not for sweeping an address book.
    emails_per_minute: float = 20.0
    email_burst: int = 10
    # Every API call, per person (per IP when signed out). Loose on purpose: a
    # page load sends many calls at once, and this exists only to stop scripts.
    requests_per_minute: float = 120.0
    request_burst: int = 40
    # Answers written at once, across servers with REDIS_URL (else per process);
    # the rest wait, then hear "busy". 20 on a 10-connection pool served 50
    # askers locally; lower it to match the outside APIs' plans. 0 turns a cap off.
    chat_max_concurrent: int = 20
    chat_queue_wait_seconds: float = 30
    # Answers one person may have being written at once: more is a script or a
    # pile of tabs, and each holds model calls and a server slot.
    chat_max_concurrent_per_person: int = 2
    # AI spend in dollars on answering models only (security/ai_spend.py); 0 = off.
    # Sized for a $100/month Claude credit and 30 people, with $10 kept as margin.
    ai_company_dollars_per_day: float = 3.00
    ai_person_dollars_per_day: float = 0.30
    # (model id, $ per million input, $ per million output)
    ai_prices: tuple[tuple[str, float, float], ...] = ()
    # (model id, person in, person out, company in, company out) tokens a minute.
    ai_token_limits: tuple[tuple[str, int, int, int, int], ...] = ()

    @classmethod
    def from_env(cls) -> "RateLimitSettings":
        def dollars(name: str, default: float) -> float:
            try:
                value = float(os.getenv(name) or default)
            except ValueError:
                return default
            return max(0.0, value)
        def rate(name: str, default: float) -> float:
            try:
                value = float(os.getenv(name) or default)
            except ValueError:
                return default
            return value if value > 0 else default

        return cls(
            enabled=env_bool("RATE_LIMIT_ENABLED", cls.enabled),
            redis_url=(os.getenv("REDIS_URL") or "").strip() or None,
            questions_per_minute=rate(
                "RATE_LIMIT_QUESTIONS_PER_MINUTE", cls.questions_per_minute
            ),
            question_burst=_env_positive_int(
                "RATE_LIMIT_QUESTION_BURST", cls.question_burst
            ),
            org_questions_per_member=rate(
                "RATE_LIMIT_ORG_QUESTIONS_PER_MEMBER",
                cls.org_questions_per_member,
            ),
            org_questions_min=_env_positive_int(
                "RATE_LIMIT_ORG_QUESTIONS_MIN", cls.org_questions_min
            ),
            emails_per_minute=rate(
                "RATE_LIMIT_EMAILS_PER_MINUTE", cls.emails_per_minute
            ),
            email_burst=_env_positive_int(
                "RATE_LIMIT_EMAIL_BURST", cls.email_burst
            ),
            requests_per_minute=rate(
                "RATE_LIMIT_REQUESTS_PER_MINUTE", cls.requests_per_minute
            ),
            request_burst=_env_positive_int(
                "RATE_LIMIT_REQUEST_BURST", cls.request_burst
            ),
            chat_max_concurrent=int(
                os.getenv("CHAT_MAX_CONCURRENT") or cls.chat_max_concurrent
            ),
            chat_queue_wait_seconds=float(
                os.getenv("CHAT_QUEUE_WAIT_SECONDS") or cls.chat_queue_wait_seconds
            ),
            chat_max_concurrent_per_person=int(
                os.getenv("CHAT_MAX_CONCURRENT_PER_PERSON")
                or cls.chat_max_concurrent_per_person
            ),
            ai_company_dollars_per_day=dollars(
                "AI_COMPANY_DOLLARS_PER_DAY", cls.ai_company_dollars_per_day
            ),
            ai_person_dollars_per_day=dollars(
                "AI_PERSON_DOLLARS_PER_DAY", cls.ai_person_dollars_per_day
            ),
            ai_prices=_parse_ai_prices(os.getenv("AI_PRICES") or DEFAULT_AI_PRICES),
            ai_token_limits=_parse_ai_token_limits(
                os.getenv("AI_TOKEN_LIMITS") or DEFAULT_AI_TOKEN_LIMITS
            ),
        )


def _parse_ai_prices(raw: str) -> tuple[tuple[str, float, float], ...]:
    """``model=in:out,…`` in dollars per million tokens. A malformed entry is
    skipped and logged, never guessed: an unpriced model is simply not counted."""
    prices = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            model, cost = entry.split("=", 1)
            inp, out = cost.split(":", 1)
            prices.append((model.strip().lower(), float(inp), float(out)))
        except ValueError:
            logging.getLogger(__name__).warning("AI_PRICES: ignoring %r (want model=in:out)", entry)
    return tuple(prices)


def _parse_ai_token_limits(raw: str) -> tuple[tuple[str, int, int, int, int], ...]:
    """``model=person_in:person_out:company_in:company_out,…`` tokens a minute;
    0 turns that one off. A malformed entry is skipped and logged."""
    limits = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            model, values = entry.split("=", 1)
            p_in, p_out, c_in, c_out = (max(0, int(v)) for v in values.split(":"))
            limits.append((model.strip().lower(), p_in, p_out, c_in, c_out))
        except ValueError:
            logging.getLogger(__name__).warning(
                "AI_TOKEN_LIMITS: ignoring %r (want model=person_in:person_out:company_in:company_out)",
                entry,
            )
    return tuple(limits)


@dataclass(frozen=True)
class AuthSettings:
    """Auth, session and credential-encryption configuration.

    - ``encryption_keys``: comma-separated Fernet keys; the first encrypts, all
    decrypt (rotate by prepending).
    - ``jwt_secret``: signs the session cookie; required, no default.
    - ``session_ttl_minutes``: 30 days by default (httpOnly, Secure, Lax; no refresh tokens).
    - ``magic_link_ttl_minutes``: login link lifetime.
    - ``signup_action_ttl_hours``: lifetime of the owner's approve/reject links.
    """

    encryption_keys: list[str] = field(default_factory=list)
    jwt_secret: str | None = None
    session_ttl_minutes: int = 60 * 24 * 30
    magic_link_ttl_minutes: int = 10
    signup_action_ttl_hours: int = 72
    #: Off in a self-hosted copy: its one company is made at install, and a
    #: public form would let anyone queue a second.
    signup_enabled: bool = True

    @classmethod
    def from_env(cls) -> "AuthSettings":
        raw_keys = os.getenv("AUTH_ENCRYPTION_KEYS") or ""
        keys = [key.strip() for key in raw_keys.split(",") if key.strip()]
        return cls(
            encryption_keys=keys,
            jwt_secret=os.getenv("AUTH_JWT_SECRET"),
            session_ttl_minutes=int(
                os.getenv("AUTH_SESSION_TTL_MINUTES") or cls.session_ttl_minutes
            ),
            magic_link_ttl_minutes=int(
                os.getenv("AUTH_MAGIC_LINK_TTL_MINUTES") or cls.magic_link_ttl_minutes
            ),
            signup_action_ttl_hours=int(
                os.getenv("AUTH_SIGNUP_ACTION_TTL_HOURS") or cls.signup_action_ttl_hours
            ),
            signup_enabled=env_bool("SIGNUP_ENABLED", True),
        )
