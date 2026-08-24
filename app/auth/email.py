"""Outbound email for magic links and the signup-approval queue (Phase 13).

Minimal pluggable sender — not a full provider/factory package since there is
only one real capability (send a message); the interface is a tiny function
dispatch rather than a full ABC. Backends:

- ``console`` (default) prints the link — local / self-hosted with zero
  extra dependency, per CLAUDE.md §1.
- ``smtp``  STARTTLS to a mail host. Works on a VPS or a *paid* Render
  instance. Render's **free** web services block outbound ports 25/465/587
  (Errno 101 Network is unreachable) — Gmail SMTP will never connect there.
- ``resend``  HTTPS POST to Resend (port 443, already used for OAuth/LLM).
  A Render-free-compatible path, but its sandbox sender
  (``onboarding@resend.dev``, used before a custom domain is verified) can
  only deliver to the Resend account's own email — every other recipient is
  rejected with a 403. Fine for the owner-notification email; not for a
  magic link addressed to an actual admin/employee.
- ``sendgrid``  HTTPS POST to SendGrid (port 443, same reasoning as Resend).
  Unlike Resend, SendGrid's **Single Sender Verification** lets one address be
  verified with no DNS/domain at all, and once verified it can send to ANY
  recipient — this is the free, no-domain-owned path to real delivery.

Callers schedule sends via FastAPI ``BackgroundTasks`` rather than blocking
the HTTP response on a remote round-trip.
"""

from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage

import httpx

from ..config.settings import EmailSettings
from ..core.exceptions import ConfigurationError, ProviderError

logger = logging.getLogger(__name__)

# Bound how long we wait on a hung SMTP peer; Gmail is usually faster but
# DNS + STARTTLS + AUTH can still be multi-second on a cold connection.
_SMTP_TIMEOUT_SECONDS = 20
_RESEND_URL = "https://api.resend.com/emails"
_RESEND_TIMEOUT_SECONDS = 20
_SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"
_SENDGRID_TIMEOUT_SECONDS = 20


def _dispatch(
    to: str,
    subject: str,
    body: str,
    settings: EmailSettings | None,
    *,
    html_body: str | None = None,
) -> None:
    """Shared console/smtp send, factored out so each template function stays
    a one-liner (subject + body) instead of re-implementing dispatch.

    ``html_body``, when given, is sent as a ``multipart/alternative`` next to
    the plain-text ``body`` — every real mail client renders the HTML part;
    ``body`` is what a plain-text client (or this function's ``console``
    mode) falls back to. Console mode only ever prints ``body``: there's no
    inbox to render HTML in, and the plain text already has usable URLs.
    """
    settings = settings or EmailSettings.from_env()

    if settings.sender == "console":
        print(f"\n[email:console] To: {to}\nSubject: {subject}\n{body}")
        return

    if settings.sender == "smtp":
        _send_smtp(to, subject, body, settings, html_body=html_body)
        return

    if settings.sender == "resend":
        _send_resend(to, subject, body, settings, html_body=html_body)
        return

    if settings.sender == "sendgrid":
        _send_sendgrid(to, subject, body, settings, html_body=html_body)
        return

    raise ConfigurationError(
        f"Unknown EMAIL_SENDER: {settings.sender!r} "
        "(expected 'console', 'smtp', 'resend', or 'sendgrid')"
    )


def _send_smtp(
    to: str,
    subject: str,
    body: str,
    settings: EmailSettings,
    *,
    html_body: str | None,
) -> None:
    if not (settings.smtp_host and settings.smtp_from):
        raise ConfigurationError(
            "EMAIL_SENDER=smtp requires EMAIL_SMTP_HOST and EMAIL_SMTP_FROM"
        )
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    if html_body is not None:
        message.add_alternative(html_body, subtype="html")
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
    except OSError as exc:
        if getattr(exc, "errno", None) == 101 or "unreachable" in str(exc).lower():
            raise ProviderError(
                "Failed to send email via SMTP: network unreachable. "
                "Render's free tier blocks outbound SMTP (ports 25/465/587). "
                "Set EMAIL_SENDER=resend with EMAIL_RESEND_API_KEY, or upgrade "
                "the Render instance.",
                cause=exc,
            ) from exc
        raise ProviderError(f"Failed to send email via SMTP: {exc}", cause=exc) from exc
    except (smtplib.SMTPException, TimeoutError) as exc:
        raise ProviderError(f"Failed to send email via SMTP: {exc}", cause=exc) from exc


def _send_resend(
    to: str,
    subject: str,
    body: str,
    settings: EmailSettings,
    *,
    html_body: str | None,
) -> None:
    if not settings.resend_api_key:
        raise ConfigurationError(
            "EMAIL_SENDER=resend requires EMAIL_RESEND_API_KEY"
        )
    if not settings.smtp_from:
        raise ConfigurationError(
            "EMAIL_SENDER=resend requires EMAIL_SMTP_FROM "
            "(the From address Resend will send as)"
        )
    payload: dict = {
        "from": settings.smtp_from,
        "to": [to],
        "subject": subject,
        "text": body,
    }
    if html_body is not None:
        payload["html"] = html_body
    try:
        with httpx.Client(timeout=_RESEND_TIMEOUT_SECONDS) as client:
            response = client.post(
                _RESEND_URL,
                headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Failed to send email via Resend: {exc}", cause=exc) from exc
    if response.status_code >= 400:
        raise ProviderError(
            f"Resend rejected the send ({response.status_code}) "
            f"from={settings.smtp_from!r}: {response.text}"
        )


def _send_sendgrid(
    to: str,
    subject: str,
    body: str,
    settings: EmailSettings,
    *,
    html_body: str | None,
) -> None:
    """SendGrid's v3 Mail Send API. Unlike Resend's sandbox sender, a
    SendGrid **Single Sender** (verified by clicking a confirmation link —
    no DNS/domain required) can deliver to any recipient, which is why this
    backend exists: it is the free, no-domain-owned path to real delivery
    for magic links addressed to actual admins/employees, not just the
    account owner's own inbox."""
    if not settings.sendgrid_api_key:
        raise ConfigurationError(
            "EMAIL_SENDER=sendgrid requires EMAIL_SENDGRID_API_KEY"
        )
    if not settings.smtp_from:
        raise ConfigurationError(
            "EMAIL_SENDER=sendgrid requires EMAIL_SMTP_FROM "
            "(the verified Single Sender address SendGrid will send as)"
        )
    content = [{"type": "text/plain", "value": body}]
    if html_body is not None:
        content.append({"type": "text/html", "value": html_body})
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": settings.smtp_from},
        "subject": subject,
        "content": content,
    }
    try:
        with httpx.Client(timeout=_SENDGRID_TIMEOUT_SECONDS) as client:
            response = client.post(
                _SENDGRID_URL,
                headers={"Authorization": f"Bearer {settings.sendgrid_api_key}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise ProviderError(f"Failed to send email via SendGrid: {exc}", cause=exc) from exc
    if response.status_code >= 400:
        raise ProviderError(
            f"SendGrid rejected the send ({response.status_code}) "
            f"from={settings.smtp_from!r}: {response.text}"
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


def send_workspace_invite_email(
    to: str,
    link: str,
    *,
    workspace_name: str,
    inviter_email: str | None = None,
    settings: EmailSettings | None = None,
) -> None:
    """Sent when an existing org member is added to someone's personal
    sub-workspace (Workspace-within-a-Workspace). Membership is already
    granted in the DB by the time this fires, so this is a courtesy
    notification with a sign-in shortcut, never a gate: if the link's token
    expires unused, the person still has access and can sign in the
    ordinary way from the login page, where the workspace is already
    waiting in their list."""
    who = f"{inviter_email} added you" if inviter_email else "You've been added"
    body = (
        f'{who} to the "{workspace_name}" workspace.\n\n'
        f"Click to sign in (expires shortly, single use):\n\n{link}\n\n"
        "If this link has expired, just sign in normally from the login "
        "page — the workspace will already be there.\n"
    )
    _dispatch(to, f'You’ve been added to "{workspace_name}"', body, settings)


def send_workspace_invite_email_safe(
    to: str, link: str, *, workspace_name: str, inviter_email: str | None = None
) -> None:
    """Background-task wrapper: never raise into the request lifecycle."""
    try:
        send_workspace_invite_email(
            to, link, workspace_name=workspace_name, inviter_email=inviter_email
        )
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Workspace-invite email to %s failed: %s", to, exc)


def send_scheduler_report_email(
    to: str,
    report_text: str,
    *,
    provider: str,
    frequency: str,
    scheduler_prompt: str,
    settings: EmailSettings | None = None,
) -> None:
    """Deliver one run of a user's recurring activity report.

    The subject names the service and cadence rather than the free-text
    prompt: prompts are sentences ("flag anything stuck in review more than
    3 days"), which make an unreadable subject line and can be long enough
    to be truncated mid-word by a mail client. The prompt is echoed in the
    footer instead, so a reader who forgot which of their schedulers this is
    can tell — and, since a user may have several on the same service, that
    footer is the only thing distinguishing two otherwise-identical mails.
    """
    body = (
        f"{report_text.strip()}\n\n"
        "—\n"
        f"This is your {frequency} {provider} report, generated from your "
        f"standing request:\n"
        f'  "{scheduler_prompt.strip()}"\n'
    )
    _dispatch(to, f"Your {frequency} {provider} report", body, settings)


def send_scheduler_report_email_safe(
    to: str,
    report_text: str,
    *,
    provider: str,
    frequency: str,
    scheduler_prompt: str,
) -> None:
    """Worker wrapper: a mail failure must not fail the scheduler run.

    Swallowing here is deliberate, not laziness. The run already did its
    expensive work (fetch + LLM); letting a transient mail error propagate
    would mark the scheduler failed and retry the *whole* run later, which
    on a partial mail outage means the user gets the same report twice.
    A dropped report is logged and the cycle moves on.
    """
    try:
        send_scheduler_report_email(
            to,
            report_text,
            provider=provider,
            frequency=frequency,
            scheduler_prompt=scheduler_prompt,
        )
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Scheduler report email to %s failed: %s", to, exc)


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


# Inline styles only — most mail clients (Gmail, Outlook, Apple Mail) strip
# <style> blocks or gradients, so every rule that matters is set directly on
# the element, and `background-color` is a plain fallback next to the
# gradient. Colors are the same tokens as frontend/app/globals.css's
# "Harbor Desk" palette (--accent/--accent-strong for approve,
# --ink/--ink-muted grays for the summary rows).
_EMAIL_APPROVE_BUTTON_STYLE = (
    "display:block;text-align:center;text-decoration:none;"
    "font-family:-apple-system,'Segoe UI',Arial,sans-serif;font-weight:700;font-size:14px;"
    "padding:12px 0;border-radius:10px;color:#f4fffd;"
    "background-color:#0f7a74;"
    "background-image:linear-gradient(145deg,#14938c 0%,#0b5f5a 100%);"
)
_EMAIL_REJECT_BUTTON_STYLE = (
    "display:block;text-align:center;text-decoration:none;"
    "font-family:-apple-system,'Segoe UI',Arial,sans-serif;font-weight:700;font-size:14px;"
    "padding:12px 0;border-radius:10px;color:#fff6f0;"
    "background-color:#9a4620;"
    "background-image:linear-gradient(145deg,#c4622f 0%,#9a4620 100%);"
)


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
    button opens a confirmation page — clicking it does not itself approve/
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
    safe_email = html.escape(email)
    safe_company = html.escape(company_name)
    html_body = f"""\
<div style="font-family:-apple-system,'Segoe UI',Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#12201e;">
  <p style="font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#0b5f5a;margin:0 0 8px;">New organization request</p>
  <h2 style="margin:0 0 16px;font-size:20px;">{safe_company}</h2>
  <table role="presentation" style="width:100%;border-collapse:collapse;margin-bottom:20px;font-size:14px;">
    <tr><td style="padding:6px 0;color:#5a6d69;">Email</td><td style="padding:6px 0;text-align:right;font-weight:600;">{safe_email}</td></tr>
    <tr><td style="padding:6px 0;color:#5a6d69;border-top:1px solid #d3dedb;">Company</td><td style="padding:6px 0;text-align:right;font-weight:600;border-top:1px solid #d3dedb;">{safe_company}</td></tr>
  </table>
  <table role="presentation" style="width:100%;"><tr>
    <td style="width:50%;padding-right:8px;">
      <a href="{approve_link}" style="{_EMAIL_APPROVE_BUTTON_STYLE}">Approve</a>
    </td>
    <td style="width:50%;padding-left:8px;">
      <a href="{reject_link}" style="{_EMAIL_REJECT_BUTTON_STYLE}">Reject</a>
    </td>
  </tr></table>
  <p style="margin-top:20px;font-size:12px;color:#8a9a95;">Each button opens a confirmation page — nothing happens until you confirm there.</p>
</div>
"""
    _dispatch(to, f"New org request: {company_name}", body, settings, html_body=html_body)


def send_signup_request_notification_email_safe(
    to: str, email: str, company_name: str, approve_link: str, reject_link: str
) -> None:
    try:
        send_signup_request_notification_email(
            to, email, company_name, approve_link, reject_link
        )
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Signup-request notification email to %s failed: %s", to, exc)
