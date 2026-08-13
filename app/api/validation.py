"""Length bounds for user-supplied strings that reach the database.

Every text column here is Postgres ``TEXT``, i.e. effectively unbounded, and no
route checked a length — so a signup could store a multi-megabyte
``company_name``, which then gets rendered into the owner's notification email
and the approve/reject confirmation page. Injection is already handled (both
escape via ``html.escape``, and Python's ``EmailMessage`` refuses a newline in a
header — verified), so this is about size and about a UI that a single field can
make unusable, not about script execution.

``app/api/chat.py``'s ``MAX_QUESTION_CHARS`` set this precedent for the one field
where an oversized value caused a hard failure (the embedding model 400s). These
are the same idea applied to the fields that were simply never checked. Limits
are generous — comfortably above any real value — because the point is to have a
ceiling at all, not to police what people type.
"""

from __future__ import annotations

from fastapi import HTTPException

# An organisation or space name shown in navigation and headings.
MAX_NAME_CHARS = 200
# An email address. RFC 5321 caps a path at 256; this is deliberately close.
MAX_EMAIL_CHARS = 320
# A pasted Drive folder URL. Real ones are ~100 chars; query strings can add.
MAX_URL_CHARS = 2000
# A free-text rejection reason recorded on a signup request.
MAX_REASON_CHARS = 2000


def bounded(
    value: str,
    *,
    field: str,
    limit: int,
    required: bool = True,
) -> str:
    """Return ``value`` unchanged, or raise 400 if empty/too long.

    Rejects rather than truncating: silently storing a shortened name would make
    the API's response disagree with what the caller sent, and a truncated URL is
    just a broken URL. A 400 naming the field and both numbers is something a
    caller can act on.
    """
    if required and not value:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    if len(value) > limit:
        raise HTTPException(
            status_code=400,
            detail=f"{field} is too long ({len(value)} characters, limit {limit}).",
        )
    return value
