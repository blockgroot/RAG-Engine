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
    AttachmentError,
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
    file: UploadFile = File(...),
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    # Parsing is CPU work on member-supplied bytes, so it is rate limited like
    # a question rather than left open.
    check_rate_limit(f"attach:{session.org_id}:{session.user_id}")
    _guard(conversation_id, session, workspace_id)

    settings = AttachmentSettings.from_env()
    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=400, detail="That upload had no filename")
    if kind_for(filename) is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"I can't read {filename}. Supported: "
                f"{', '.join('.' + e for e in SUPPORTED_EXTENSIONS)}"
            ),
        )

    if (
        count_attachments(
            org_id=session.org_id,
            conversation_id=conversation_id,
            user_id=session.user_id,
        )
        >= settings.max_per_conversation
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                f"That chat already has {settings.max_per_conversation} files. "
                "Remove one, or start a new chat."
            ),
        )

    # Read with ONE byte of headroom so an oversized file is rejected by the
    # limit rather than by whatever breaks first downstream, and so the check
    # cannot be defeated by a wrong or absent Content-Length.
    data = await file.read(settings.max_bytes + 1)
    if len(data) > settings.max_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{filename} is larger than "
                f"{settings.max_bytes // (1024 * 1024)}MB."
            ),
        )
    if not data:
        raise HTTPException(status_code=400, detail=f"{filename} is empty")

    try:
        text, truncated = extract_text(
            filename,
            data,
            max_chars=settings.max_chars,
            max_pdf_pages=settings.max_pdf_pages,
            max_csv_rows=settings.max_csv_rows,
        )
    except AttachmentError as exc:
        # The message is written for the uploader ("this is a scan", "this is
        # password-protected"), so it is passed through rather than replaced
        # with a generic 400 that tells them nothing to do next.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Attachment: unexpected failure parsing %s", filename)
        raise HTTPException(
            status_code=400, detail=f"{filename} could not be read"
        ) from exc
    finally:
        del data  # the bytes are not needed past this point and are not kept

    saved = save_attachment(
        org_id=session.org_id,
        conversation_id=conversation_id,
        user_id=session.user_id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        content=text,
        truncated=truncated,
    )
    return {
        "id": saved.id,
        "filename": saved.filename,
        "char_count": saved.char_count,
        "truncated": saved.truncated,
    }


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
