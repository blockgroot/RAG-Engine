"""Send one real email through the configured sender, and FAIL LOUDLY.

Why this exists: every production send goes through a ``*_safe`` wrapper that
catches and logs its exception, because a failed notification must not cost a
report that has already been generated (``app/schedulers/runner.py``). That is
the right behaviour in production and the wrong behaviour when you are trying
to find out whether email works at all — a misconfigured sender looks
successful in the logs.

This calls the UNWRAPPED sender, so a bad key, an unverified From address, or a
blocked SMTP port surfaces as a traceback instead of a warning.

    python scripts/verify_email.py you@example.com
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config.settings import EmailSettings  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    to = sys.argv[1]

    settings = EmailSettings.from_env()
    print(f"EMAIL_SENDER = {settings.sender!r}")
    print(f"From         = {settings.smtp_from!r}")
    print(f"To           = {to!r}\n")

    if settings.sender == "console":
        print(
            "EMAIL_SENDER is 'console' — nothing will be delivered, the body is\n"
            "printed below instead. Set EMAIL_SENDER=sendgrid (plus\n"
            "EMAIL_SENDGRID_API_KEY and a verified EMAIL_SMTP_FROM) for real\n"
            "delivery. Render's free tier blocks outbound SMTP, so 'smtp' will\n"
            "not work there.\n"
        )

    # The UNWRAPPED report sender — not send_scheduler_report_email_safe. This
    # is deliberately the real scheduler notification rather than a generic
    # test message: it exercises the same subject, plain-text and HTML bodies,
    # and From address a member will actually receive, so "the test worked but
    # reports don't arrive" cannot happen.
    from app.auth.email import send_scheduler_report_email

    send_scheduler_report_email(
        to,
        provider="slack",
        frequency="weekly",
        scheduler_prompt=(
            "Delivery test from scripts/verify_email.py — if this is in your "
            "inbox, scheduled reports can reach real recipients."
        ),
        link="https://example.invalid/schedulers/reports/test",
        item_count=3,
        space_name=None,
        settings=settings,
    )
    print("Sender returned without raising.")
    if settings.sender != "console":
        print(
            "\nThat means the provider ACCEPTED the message. Accepted is not the\n"
            "same as delivered — check the inbox, and the spam folder (a "
            "SendGrid\nSingle Sender has no SPF/DKIM alignment, so first "
            "messages often land\nthere)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
