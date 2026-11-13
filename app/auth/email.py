"""Outbound email: magic links, signup approval, invites, report notices.

Backends: ``console`` (default; prints), ``smtp`` (STARTTLS; Render's free
tier blocks its ports), ``resend`` (HTTPS; its sandbox sender reaches only
the account's own address) and ``sendgrid`` (HTTPS; a verified Single Sender
reaches anyone with no domain). Callers send from ``BackgroundTasks``.
"""

from __future__ import annotations

import html
import logging
import smtplib
from collections.abc import Sequence
from email.message import EmailMessage
from email.utils import parseaddr

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
    """Shared send for every template.

    ``html_body`` goes as ``multipart/alternative`` beside the plain ``body``;
    console mode prints only ``body``.
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
    """SendGrid v3 Mail Send: a verified Single Sender reaches any recipient, no domain needed."""
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
    # SendGrid takes the display name as its own field, so
    # "Handbook <a@b.com>" is split; a bare address is sent as before.
    name, address = parseaddr(settings.smtp_from)
    sender = {"email": address or settings.smtp_from.strip()}
    if name:
        sender["name"] = name
    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": sender,
        "subject": subject,
        "content": content,
        # Off: click tracking rewrote sign-in links through sendgrid.net, passing
        # their single-use token to a third party first.
        "tracking_settings": {"click_tracking": {"enable": False, "enable_text": False}},
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


def _link_email(*, title: str, lead: str, button: str, link: str, note: str) -> str:
    """One button, not a bare URL: the address is long and means nothing to a
    reader. The URL is still written small underneath for a client that drops
    the button."""
    href = html.escape(link, quote=True)
    return f"""\
<div style="font-family:-apple-system,'Segoe UI',Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#12201e;">
  <p style="font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#0b5f5a;margin:0 0 8px;">Handbook</p>
  <h2 style="margin:0 0 12px;font-size:20px;">{html.escape(title)}</h2>
  <p style="margin:0 0 20px;font-size:14px;line-height:1.5;color:#3d4f4b;">{html.escape(lead)}</p>
  <a href="{href}" style="{_EMAIL_APPROVE_BUTTON_STYLE}">{html.escape(button)}</a>
  <p style="margin:20px 0 0;font-size:12px;line-height:1.5;color:#8a9a95;">{html.escape(note)}</p>
  <p style="margin:12px 0 0;font-size:11px;line-height:1.4;color:#8a9a95;word-break:break-all;">Button not working? Paste this into your browser:<br><a href="{href}" style="color:#0b5f5a;">{html.escape(link)}</a></p>
</div>
"""


def send_magic_link_email(to: str, link: str, *, settings: EmailSettings | None = None) -> None:
    body = f"Click to sign in (expires shortly, single use):\n\n{link}\n"
    html_body = _link_email(
        title="Sign in to Handbook",
        lead="Use the button below to sign in. The link works once and expires shortly.",
        button="Sign in",
        link=link,
        note="If you didn't ask to sign in, ignore this email. Nobody can sign in without this link.",
    )
    _dispatch(to, "Your sign-in link", body, settings, html_body=html_body)


def send_magic_link_email_safe(to: str, link: str) -> None:
    """Background-task wrapper: never raise into the request lifecycle."""
    try:
        send_magic_link_email(to, link)
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Magic-link email to %s failed: %s", to, exc)



def send_email_change_verification(to: str, link: str, *, settings: EmailSettings | None = None) -> None:
    body = (
        "Someone asked to change their Handbook sign-in email to this address.\n\n"
        f"If it was you, confirm here (expires shortly, single use):\n\n{link}\n\n"
        "If it wasn't, ignore this email -- nothing changes until the link is used.\n"
    )
    html_body = _link_email(
        title="Confirm your new sign-in email",
        lead="Someone asked to change their Handbook sign-in email to this address. If it was you, confirm below. The link works once and expires shortly.",
        button="Confirm email",
        link=link,
        note="If it wasn't you, ignore this email. Nothing changes until the link is used.",
    )
    _dispatch(to, "Confirm your new sign-in email", body, settings, html_body=html_body)


def send_email_change_verification_safe(to: str, link: str) -> None:
    """Background-task wrapper: never raise into the request lifecycle."""
    try:
        send_email_change_verification(to, link)
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Email-change verification to %s failed: %s", to, exc)


def send_email_changed_notice_safe(to: str, new_email: str) -> None:
    """Tell the OLD address its account moved -- the one party who would notice
    a change they did not make."""
    body = (
        f"Your Handbook sign-in email was changed to {new_email}.\n\n"
        "Documents shared with this address stay readable to you. If you did not "
        "make this change, contact your Handbook admin.\n"
    )
    try:
        _dispatch(to, "Your sign-in email was changed", body, None)
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Email-changed notice to %s failed: %s", to, exc)


def send_release_email(
    to: str, *, release, current: str | None, link: str, settings: EmailSettings | None = None
) -> None:
    """A newer Handbook is out: what changed, what to do, where to do it."""
    urgent = bool(getattr(release, "security", False))
    subject = (
        f"Security update: Handbook {release.version} is available"
        if urgent
        else f"Handbook {release.version} is available"
    )
    points = "\n".join(f"  - {p}" for p in (release.notes or [])[:8])
    lines = [
        f"A new version of Handbook, {release.version}, is out."
        + (f" This server runs {current}." if current else ""),
        "",
    ]
    if urgent:
        lines += ["It includes a security fix. Please update soon.", ""]
    if points:
        lines += ["What's new:", points, ""]
    if getattr(release, "manual_steps", False):
        lines += ["This update needs a few steps on your server first; the release notes list them.", ""]
    lines += [f"Read the notes and update from Handbook:\n{link}"]
    if getattr(release, "notes_url", None):
        lines += ["", f"Full release notes: {release.notes_url}"]
    lines += ["", "You get this once per version because you are a Handbook admin."]
    _dispatch(to, subject, "\n".join(lines) + "\n", settings)


def send_release_email_safe(to: str, *, release, current: str | None, link: str) -> bool:
    """Never raises; says whether the mail went."""
    try:
        send_release_email(to, release=release, current=current, link=link)
        return True
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Release email to %s failed: %s", to, exc)
        return False


def send_workspace_invite_email(
    to: str,
    link: str,
    *,
    workspace_name: str,
    inviter_email: str | None = None,
    settings: EmailSettings | None = None,
) -> None:
    """Sent when a member is added to someone's space.

    Access is already granted; the link is a shortcut, never a gate.
    """
    who = f"{inviter_email} added you" if inviter_email else "You've been added"
    body = (
        f'{who} to the "{workspace_name}" workspace.\n\n'
        f"Click to sign in (expires shortly, single use):\n\n{link}\n\n"
        "If this link has expired, just sign in normally from the login "
        "page — the workspace will already be there.\n"
    )
    html_body = _link_email(
        title=f'{who} to "{workspace_name}"',
        lead="Sign in to open the space. The link works once and expires shortly.",
        button="Open the space",
        link=link,
        note="If the link has expired, sign in from the login page. The space will already be there.",
    )
    _dispatch(to, f'You’ve been added to "{workspace_name}"', body, settings, html_body=html_body)


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
    *,
    provider: str,
    frequency: str,
    scheduler_prompt: str,
    link: str,
    item_count: int,
    space_name: str | None = None,
    settings: EmailSettings | None = None,
) -> None:
    """Tell someone their report is ready and link to it.

    The report stays in the app: mail flattens its layout and links, and a
    failed send then costs only a notice. No excerpt: a two-line preview of a
    summary reads as the whole answer.
    """
    where = f" · {space_name}" if space_name else ""
    activity = (
        f"{item_count} item{'' if item_count == 1 else 's'} of activity"
        if item_count
        else "No activity in this period"
    )
    subject = f"Your {frequency} {provider} report is ready"
    body = (
        f"Your {frequency} {provider} report{where} has been generated.\n\n"
        f"Covered: {activity}\n\n"
        f"Read it here:\n{link}\n\n"
        f"Generated from your standing request:\n  \"{scheduler_prompt.strip()}\"\n"
    )

    safe_prompt = html.escape(scheduler_prompt.strip())
    safe_where = html.escape(space_name or "Company-wide")
    safe_provider = html.escape(provider)
    html_body = f"""\
<div style="font-family:-apple-system,'Segoe UI',Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#12201e;">
  <p style="font-size:12px;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#0b5f5a;margin:0 0 8px;">{html.escape(frequency)} report</p>
  <h2 style="margin:0 0 16px;font-size:20px;">Your {safe_provider} report is ready</h2>
  <table role="presentation" style="width:100%;border-collapse:collapse;margin-bottom:20px;font-size:14px;">
    <tr><td style="padding:6px 0;color:#5a6d69;">Source</td><td style="padding:6px 0;text-align:right;font-weight:600;">{safe_provider}</td></tr>
    <tr><td style="padding:6px 0;color:#5a6d69;border-top:1px solid #d3dedb;">Space</td><td style="padding:6px 0;text-align:right;font-weight:600;border-top:1px solid #d3dedb;">{safe_where}</td></tr>
    <tr><td style="padding:6px 0;color:#5a6d69;border-top:1px solid #d3dedb;">Covered</td><td style="padding:6px 0;text-align:right;font-weight:600;border-top:1px solid #d3dedb;">{html.escape(activity)}</td></tr>
  </table>
  <a href="{link}" style="{_EMAIL_APPROVE_BUTTON_STYLE}">Read the full report</a>
  <p style="margin-top:20px;font-size:12px;color:#8a9a95;">Your standing request: &ldquo;{safe_prompt}&rdquo;</p>
</div>
"""
    _dispatch(to, subject, body, settings, html_body=html_body)


def send_scheduler_report_email_safe(
    to: str,
    *,
    provider: str,
    frequency: str,
    scheduler_prompt: str,
    link: str,
    item_count: int,
    space_name: str | None = None,
) -> bool:
    """Send a report notice; a mail failure must not fail the scheduler run.

    Returns whether it was accepted. Swallows errors: raising would retry the
    run and could send the report twice.
    """
    try:
        send_scheduler_report_email(
            to,
            provider=provider,
            frequency=frequency,
            scheduler_prompt=scheduler_prompt,
            link=link,
            item_count=item_count,
            space_name=space_name,
        )
        return True
    except (ConfigurationError, ProviderError) as exc:
        logger.warning("Scheduler report email to %s failed: %s", to, exc)
        return False


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
    html_body = _link_email(
        title="Your organization is ready",
        lead="Your request was approved. Sign in to connect your first tool. The link works once and expires shortly.",
        button="Sign in",
        link=link,
        note="If the link has expired, request a new one from the login page.",
    )
    _dispatch(to, "Your organization is ready", body, settings, html_body=html_body)


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


# Inline styles only: most mail clients strip <style> and gradients, so
# `background-color` backs each gradient. Colors match frontend/app/globals.css.
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
    """Sent to the platform owner when an org-creation request lands.

    Each button opens a confirmation page, so a prefetching scanner cannot act.
    """
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
