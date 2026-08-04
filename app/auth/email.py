"""Outbound email for magic links and the signup-approval queue (Phase 13).

Minimal pluggable sender — not a full provider/factory package since there is
only one real capability (send a message) and one production backend (SMTP);
the interface is kept as a tiny function dispatch rather than a full ABC.
``console`` (default) prints the link instead of sending it, so the whole
login flow works locally / self-hosted with zero external dependency, per
CLAUDE.md §1.

SMTP sends are intentionally simple: connect → STARTTLS → login → send. That
round-trip to a remote host (e.g. Gmail) routinely takes several seconds, so
callers schedule these via FastAPI ``BackgroundTasks`` rather than blocking
the HTTP response on it.
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


def _dispatch(to: str, subject: str, body: str, settings: EmailSettings | None) -> None:
    """Shared console/smtp send, factored out so each template function stays
    a one-liner (subject + body) instead of re-implementing dispatch."""
    settings = settings or EmailSettings.from_env()

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
            raise ProviderError(f"Failed to send email via SMTP: {exc}", cause=exc) from exc
        return

    raise ConfigurationError(
        f"Unknown EMAIL_SENDER: {settings.sender!r} (expected 'console' or 'smtp')"
    )


def send_magic_link_email(to: str, link: str, *, settings: EmailSettings | None = None) -> None:
    body = f"Click to sign in (expires shortly, single use):\n\n{link}\n"
    _dispatch(to, "Your sign-in link", body, settings)


def send_magic_link_email_safe(to: str, link: str) -> None:
    """Background-task wrapper: never raise into the request lifecycle."""
    try:
        send_magic_link_email(to, link)
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Magic-link email to %s failed: %s", to, exc)


def send_signup_approved_email(
    to: str, link: str, *, settings: EmailSettings | None = None
) -> None:
    """Sent after a signup request is approved (via its one-click email
    link) — the org+admin already exist by the time this is called; this
    link is the requester's first sign-in."""
    body = (
        "Good news — your organization has been approved and is ready.\n\n"
        f"Click to sign in (expires shortly, single use):\n\n{link}\n"
    )
    _dispatch(to, "Your organization is ready", body, settings)


def send_signup_approved_email_safe(to: str, link: str) -> None:
    try:
        send_signup_approved_email(to, link)
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Signup-approved email to %s failed: %s", to, exc)


def send_signup_rejected_email(
    to: str, reason: str | None = None, *, settings: EmailSettings | None = None
) -> None:
    """Sent after a signup request is rejected (via its one-click email link)."""
    body = "We're not able to approve your request to create an organization at this time.\n"
    if reason:
        body += f"\nReason: {reason}\n"
    _dispatch(to, "About your organization request", body, settings)


def send_signup_rejected_email_safe(to: str, reason: str | None = None) -> None:
    try:
        send_signup_rejected_email(to, reason)
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Signup-rejected email to %s failed: %s", to, exc)


def send_signup_request_notification_email(
    to: str,
    email: str,
    company_name: str,
    approve_link: str,
    reject_link: str,
    *,
    settings: EmailSettings | None = None,
) -> None:
    """Sent to the platform owner (``EmailSettings.owner_notification_email``)
    when a new org-creation request lands in ``org_signup_requests``. Each
    link opens a confirmation page — clicking it does not itself approve/
    reject, so a mail client or scanner prefetching the link can't trigger
    the action (see ``app/api/auth.py``'s GET-then-POST confirm routes)."""
    body = (
        f"New organization request:\n\n"
        f"  Email:   {email}\n"
        f"  Company: {company_name}\n\n"
        f"Approve: {approve_link}\n"
        f"Reject:  {reject_link}\n\n"
        "Each link opens a confirmation page — nothing happens until you "
        "click the button on that page.\n"
    )
    _dispatch(to, f"New org request: {company_name}", body, settings)


def send_signup_request_notification_email_safe(
    to: str, email: str, company_name: str, approve_link: str, reject_link: str
) -> None:
    try:
        send_signup_request_notification_email(
            to, email, company_name, approve_link, reject_link
        )
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Signup-request notification email to %s failed: %s", to, exc)
