"""Upload a file into a conversation and ask about it.

Only the uploader, in that conversation, can read it. The bytes and text go
to the object store as private assets (signed, expiring URLs), never a public link.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from ..attachments import (
    SUPPORTED_EXTENSIONS,
    token_rejection_reason,
    AttachmentError,
    AttachmentStorageError,
    count_attachments,
    drop_stored,
    remove_attachment_row,
    extract_text,
    kind_for,
    list_attachments,
    save_attachment,
)
from ..core.exceptions import AuthError
from ..config.settings import AttachmentSettings
from ..guard.live import is_flagged, score_attachment
from ..workspaces import assert_member
from ..auth.session import SessionClaims
from .deps import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat/conversations", tags=["attachments"])


def _guard(
    conversation_id: str, session: SessionClaims, workspace_id: str | None
) -> None:
    """The conversation must be this org's, this scope's and this person's.

    Reuses `chat._conversation_belongs_to_scope` so the rule has one copy.
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
    """Upload one or more files; some may land while others are refused.

    Each refusal names its file and reason. Only request-level faults (rate
    limit, not their conversation) fail the whole request.
    """
    # Parsing is CPU work on member-supplied bytes; the per-person limit every
    # route takes (`security.rate_limit.limit_request`) bounds it.
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
    """``(attachment, None)`` or ``(None, reason)``; never raises."""
    if kind_for(filename) is None:
        return None, (
            f"I can't read {filename}. Supported: "
            f"{', '.join('.' + e for e in SUPPORTED_EXTENSIONS)}"
        )

    # Read one byte past the limit, so the size check does not trust Content-Length.
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
            # Written for the uploader, so passed through.
            return None, str(exc)
        except Exception:  # noqa: BLE001
            logger.exception("Attachment: unexpected failure parsing %s", filename)
            return None, f"{filename} could not be read"

        # The PROMPT gate, checked before anything is stored: there is no point
        # putting a file in the object store that can never reach a prompt.
        too_long = token_rejection_reason(filename, text, settings)
        if too_long:
            return None, too_long

        # Scored once, off the event loop (a blocking Groq call). A flagged file
        # is still accepted: members upload vendor PDFs legitimately.
        injection_score = await run_in_threadpool(score_attachment, text)

        try:
            attachment = save_attachment(
                org_id=session.org_id,
                conversation_id=conversation_id,
                user_id=session.user_id,
                filename=filename,
                content_type=upload.content_type or "application/octet-stream",
                content=text,
                truncated=truncated,
                # The original bytes go to the object store so a lost asset can be re-extracted.
                data=data,
                injection_score=injection_score,
            )
        except AttachmentStorageError:
            logger.exception("Attachment: storage failure for %s", filename)
            return None, f"{filename} could not be stored. Try again in a moment."

        # Tables the file already has, for Chart mode. Off the event loop (a
        # PDF's table finder is slow) and never a reason to refuse the file.
        from ..doctables.uploads import read_upload_tables

        await run_in_threadpool(
            read_upload_tables, attachment.id, org_id=session.org_id,
            user_id=session.user_id, filename=filename, data=data, text=text,
            max_pdf_pages=settings.max_pdf_pages,
        )
    finally:
        del data  # not kept in this process past the upload

    return {
        "id": attachment.id,
        "filename": attachment.filename,
        "char_count": attachment.char_count,
        "truncated": attachment.truncated,
        "flagged": is_flagged(attachment.injection_score),
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
                "flagged": is_flagged(a.injection_score),
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
    background: BackgroundTasks,
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """Idempotent: a file already gone is ``deleted: false``, never a 404.

    Owner-scoped, so the answer is the same for an id that never existed.
    """
    _guard(conversation_id, session, workspace_id)
    removed, key = remove_attachment_row(
        attachment_id=attachment_id,
        org_id=session.org_id,
        conversation_id=conversation_id,
        user_id=session.user_id,
    )
    if removed:
        # After the response: the row is the removal, the store is cleanup.
        background.add_task(drop_stored, key)
    return {"deleted": removed}
