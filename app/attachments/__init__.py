"""In-chat file attachments: extract text, keep it with the conversation.

Never embedded, never written to `documents`, and the uploaded bytes are
discarded once parsed -- see the `conversation_attachments` block in
`app/db/schema.sql` for why each of those is a decision rather than a shortcut.
"""

from .blobstore import (
    AttachmentStorageError,
    list_keys,
    delete_object,
    plaintext_key,
    signed_url,
)
from .limits import TOKEN_EXEMPT_KINDS, token_rejection_reason
from .extract import (
    SUPPORTED_EXTENSIONS,
    AttachmentError,
    extract_text,
    kind_for,
)
from .store import (
    list_orphan_objects,
    DEFAULT_ATTACHMENT_TTL_HOURS,
    Attachment,
    count_attachments,
    delete_attachment,
    list_attachments,
    load_attachment_texts,
    purge_expired_attachments,
    save_attachment,
)

__all__ = [
    "Attachment",
    "AttachmentError",
    "AttachmentStorageError",
    "SUPPORTED_EXTENSIONS",
    "TOKEN_EXEMPT_KINDS",
    "token_rejection_reason",
    "DEFAULT_ATTACHMENT_TTL_HOURS",
    "count_attachments",
    "delete_attachment",
    "delete_object",
    "list_keys",
    "list_orphan_objects",
    "plaintext_key",
    "signed_url",
    "extract_text",
    "kind_for",
    "list_attachments",
    "purge_expired_attachments",
    "load_attachment_texts",
    "save_attachment",
]
