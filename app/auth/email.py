"""Outbound email for magic links (Phase 13).

Minimal pluggable sender — not a full provider/factory package since there is
only one real capability (send a message) and one production backend (SMTP);
the interface is kept as a tiny function dispatch rather than a full ABC.
``console`` (default) prints the link instead of sending it, so the whole
login flow works locally / self-hosted with zero external dependency, per
CLAUDE.md §1.

SMTP sends are intentionally simple: connect → STARTTLS → login → send. That
round-trip to a remote host (e.g. Gmail) routinely takes several seconds, so
callers should schedule ``send_magic_link_email`` via FastAPI
``BackgroundTasks`` rather than blocking the HTTP response on it.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from ..config.settings import EmailSettings
from ..core.exceptions import ConfigurationError, ProviderError

logger = logging.getLogger(__name__)

# Bound how long we wait on a hung SMTP peer; Gmail is usually faster but
# DNS + STARTTLS + AUTH can still be multi-second on a cold connection.
_SMTP_TIMEOUT_SECONDS = 20


def send_magic_link_email(to: str, link: str, *, settings: EmailSettings | None = None) -> None:
    settings = settings or EmailSettings.from_env()
    subject = "Your sign-in link"
    body = f"Click to sign in (expires shortly, single use):\n\n{link}\n"

    if settings.sender == "console":
        print(f"\n[email:console] To: {to}\nSubject: {subject}\n{body}")
        return

    if settings.sender == "smtp":
        if not (settings.smtp_host and settings.smtp_from):
            raise ConfigurationError(
                "EMAIL_SENDER=smtp requires EMAIL_SMTP_HOST and EMAIL_SMTP_FROM"
            )
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(
                settings.smtp_host,
                settings.smtp_port or 587,
                timeout=_SMTP_TIMEOUT_SECONDS,
            ) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                if settings.smtp_username and settings.smtp_password:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            raise ProviderError(
                f"Failed to send magic-link email via SMTP: {exc}", cause=exc
            ) from exc
        return

    raise ConfigurationError(
        f"Unknown EMAIL_SENDER: {settings.sender!r} (expected 'console' or 'smtp')"
    )


def send_magic_link_email_safe(to: str, link: str) -> None:
    """Background-task wrapper: never raise into the request lifecycle."""
    try:
        send_magic_link_email(to, link)
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Magic-link email to %s failed: %s", to, exc)
