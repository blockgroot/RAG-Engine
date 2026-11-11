"""Email a self-hosted copy's admins once when a newer Handbook is out.

The bell and Admin › Updates only tell someone who opens Handbook; an admin
away for a week heard nothing for a week. The worker's hourly pass reads the
same release list the bell does and mails every admin about the newest
release, once: ``update_notices`` holds the versions already announced, and
the row is claimed BEFORE sending so two workers never both send. If every
send fails, the claim is released and the next pass tries again.

Cloud is kept current for its companies, so it announces nothing.
"""

from __future__ import annotations

import logging

from ..config.settings import ApiSettings, UpdateSettings
from .check import Release, update_status

logger = logging.getLogger(__name__)


def _claim(version: str) -> bool:
    from ..db.connection import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO update_notices (version) VALUES (%s) "
            "ON CONFLICT (version) DO NOTHING RETURNING version",
            (version,),
        ).fetchone()
    return row is not None


def _release_claim(version: str) -> None:
    from ..db.connection import get_connection

    with get_connection() as conn:
        conn.execute("DELETE FROM update_notices WHERE version = %s", (version,))


def _admin_emails() -> list[str]:
    # A self-hosted copy is one company; a release concerns the whole server,
    # so every admin on it is told. No company content is read.
    from ..db.connection import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT lower(email) FROM users WHERE role = 'admin' AND email IS NOT NULL"
        ).fetchall()
    return [r[0] for r in rows]


def _send(to: str, release: Release, current: str | None, link: str) -> bool:
    from ..auth.email import send_release_email_safe

    return send_release_email_safe(to, release=release, current=current, link=link)


def announce_new_release(settings: UpdateSettings | None = None) -> int:
    """Mail every admin about the newest release, once. Returns emails sent."""
    settings = settings or UpdateSettings.from_env()
    if settings.deployment != "selfhosted" or not settings.check_enabled:
        return 0
    status = update_status(settings)
    release = status.latest
    if release is None or not _claim(release.version):
        return 0
    base = (ApiSettings.from_env().frontend_url or "").rstrip("/")
    link = f"{base}/admin/updates" if base else "/admin/updates"
    sent = sum(_send(to, release, status.current, link) for to in _admin_emails())
    if sent == 0:
        _release_claim(release.version)
    logger.info("Release %s announced to %s admin(s)", release.version, sent)
    return sent
