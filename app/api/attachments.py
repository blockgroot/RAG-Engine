"""Upload a file into a conversation and ask about it.

The uploaded BYTES never leave this function: they are parsed to text in
memory and dropped. There is no disk write, no object store and no CDN --
which is what makes "only the uploader can see it" a property of the system
rather than a promise about a URL. A link-addressable copy would be readable
by anyone holding the link, including anyone the link is forwarded to, and
would outlive the conversation it belongs to.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..attachments import (
    SUPPORTED_EXTENSIONS,
    token_rejection_reason,
    AttachmentError,
    AttachmentStorageError,
    count_attachments,
    delete_attachment,
    extract_text,
    kind_for,
    list_attachments,
    save_attachment,
)
from ..core.exceptions import AuthError
from ..config.settings import AttachmentSettings
from ..security.rate_limit import check_rate_limit
from ..workspaces import assert_member
from ..auth.session import SessionClaims
from .deps import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat/conversations", tags=["attachments"])


def _guard(
    conversation_id: str, session: SessionClaims, workspace_id: str | None
) -> None:
    """The conversation must be this org's, this scope's AND this person's.

    Reuses `chat._conversation_belongs_to_scope` rather than re-deriving the
    rule: the owner check is the whole reason an attachment is private, and
    two copies of it would eventually disagree.
    """
    from .chat import _conversation_belongs_to_scope

    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not _conversation_belongs_to_scope(
        conversation_id, session.org_id, workspace_id, session.user_id
    ):
        raise HTTPException(
            status_code=404, detail="No such conversation for this organization"
        )


@router.post("/{conversation_id}/attachments")
async def upload_attachment(
    conversation_id: str,
    file: list[UploadFile] = File(...),
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """Upload one or more files. Some may land while others are refused.

    **Partial success is the normal outcome, not an edge case** (Onyx's
    `CategorizedFilesSnapshot`). Someone selecting five files routinely has
    one scan, one password-protected PDF and three good documents; failing the
    whole request on the first bad one makes them re-pick the good ones and
    guess which was at fault. Every refusal names the file and why.

    Only the things that are true of the REQUEST rather than of a file -- the
    rate limit, the conversation not being theirs -- still fail it outright.
    """
    # Parsing is CPU work on member-supplied bytes, so it is rate limited like
    # a question rather than left open.
    check_rate_limit(f"attach:{session.org_id}:{session.user_id}")
    _guard(conversation_id, session, workspace_id)

    settings = AttachmentSettings.from_env()
    saved: list[dict] = []
    rejected: list[dict] = []

    # Counted once, then tracked locally: re-querying per file would be a
    # round trip to learn something this loop already knows.
    remaining = settings.max_per_conversation - count_attachments(
        org_id=session.org_id,
        conversation_id=conversation_id,
        user_id=session.user_id,
    )

    for upload in file:
        filename = (upload.filename or "").strip()
        if not filename:
            rejected.append({"filename": "", "reason": "That upload had no filename"})
            continue
        if remaining <= 0:
            rejected.append(
                {
                    "filename": filename,
                    "reason": (
                        f"That chat already holds {settings.max_per_conversation} "
                        "files. Remove one, or start a new chat."
                    ),
                }
            )
            continue

        attachment, reason = await _store_one(
            upload, filename, conversation_id, session, settings
        )
        if attachment is None:
            rejected.append({"filename": filename, "reason": reason or "Could not be read"})
            continue

        saved.append(attachment)
        remaining -= 1

    return {"attachments": saved, "rejected": rejected}


async def _store_one(
    upload: UploadFile,
    filename: str,
    conversation_id: str,
    session: SessionClaims,
    settings: AttachmentSettings,
) -> tuple[dict | None, str | None]:
    """``(attachment, None)`` or ``(None, reason)``. Never raises.

    Every refusal in here is a fact about ONE file, so it comes back as a
    reason beside that file's name rather than as a status code for the whole
    upload.
    """
    if kind_for(filename) is None:
        return None, (
            f"I can't read {filename}. Supported: "
            f"{', '.join('.' + e for e in SUPPORTED_EXTENSIONS)}"
        )

    # Read with ONE byte of headroom so an oversized file is rejected by the
    # limit rather than by whatever breaks first downstream, and so the check
    # cannot be defeated by a wrong or absent Content-Length.
    data = await upload.read(settings.max_bytes + 1)
    try:
        if len(data) > settings.max_bytes:
            return None, (
                f"{filename} is larger than {settings.max_bytes // (1024 * 1024)}MB."
            )
        if not data:
            return None, f"{filename} is empty"

        try:
            text, truncated = extract_text(
                filename,
                data,
                max_chars=settings.max_chars,
                max_pdf_pages=settings.max_pdf_pages,
                max_csv_rows=settings.max_csv_rows,
            )
        except AttachmentError as exc:
            # Written for the uploader ("this is a scan", "this is
            # password-protected"), so it is passed through rather than
            # replaced with a generic message that says nothing to do next.
            return None, str(exc)
        except Exception:  # noqa: BLE001
            logger.exception("Attachment: unexpected failure parsing %s", filename)
            return None, f"{filename} could not be read"

        # The PROMPT gate, checked before anything is stored: there is no point
        # putting a file in the object store that can never reach a prompt.
        too_long = token_rejection_reason(filename, text, settings)
        if too_long:
            return None, too_long

        try:
            attachment = save_attachment(
                org_id=session.org_id,
                conversation_id=conversation_id,
                user_id=session.user_id,
                filename=filename,
                content_type=upload.content_type or "application/octet-stream",
                content=text,
                truncated=truncated,
                # The ORIGINAL bytes leave this process for the object store,
                # so a lost plaintext asset can be re-extracted rather than
                # lost with it.
                data=data,
            )
        except AttachmentStorageError:
            logger.exception("Attachment: storage failure for %s", filename)
            return None, f"{filename} could not be stored. Try again in a moment."
    finally:
        del data  # not kept in this process past the upload

    return {
        "id": attachment.id,
        "filename": attachment.filename,
        "char_count": attachment.char_count,
        "truncated": attachment.truncated,
    }, None


@router.get("/{conversation_id}/attachments")
def get_attachments(
    conversation_id: str,
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    _guard(conversation_id, session, workspace_id)
    return {
        "attachments": [
            {
                "id": a.id,
                "filename": a.filename,
                "char_count": a.char_count,
                "truncated": a.truncated,
            }
            for a in list_attachments(
                org_id=session.org_id,
                conversation_id=conversation_id,
                user_id=session.user_id,
            )
        ]
    }


@router.delete("/{conversation_id}/attachments/{attachment_id}")
def remove_attachment(
    conversation_id: str,
    attachment_id: str,
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    _guard(conversation_id, session, workspace_id)
    if not delete_attachment(
        attachment_id=attachment_id,
        org_id=session.org_id,
        conversation_id=conversation_id,
        user_id=session.user_id,
    ):
        raise HTTPException(status_code=404, detail="No such attachment")
    return {"deleted": True}
