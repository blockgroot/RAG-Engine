"""Google Docs action: create a new Doc in the asker's own Drive.

It runs on the person's own token with ``drive.file`` only: Google's
non-sensitive scope that reaches files the app creates (or the person picks),
nothing else in their Drive. Broad ``drive`` access is a restricted scope with
a yearly third-party security assessment; creating new Docs never needs it.

The new Doc is private to its creator, so its audience is the asker alone and
no private source is shown to anyone new. The connect flow also checks the
Google account IS the person's work account, so company content never lands
in a personal Drive.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from ..core.exceptions import (
    ActionError,
    ActionOutcomeUnknown,
    ActionRejected,
    OAuthReauthRequiredError,
)
from .base import Action, ActionContext, ActionResult, ActionTarget, Audience, Proposal
from .draft import clean_body, clean_title, without_title_heading

#: The one Google scope Act asks a person for.
DRIVE_FILE = "https://www.googleapis.com/auth/drive.file"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
_TIMEOUT = 20.0


class DriveCreateDoc(Action):
    name = "drive.create_doc"
    provider = "google"
    label = "Google Docs: create a doc in your Drive"
    tool = {
        "type": "function",
        "function": {
            "name": "drive_create_doc",
            "description": "Create a new Google Doc in the user's own Google Drive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_words": {
                        "type": "string",
                        "description": (
                            "The exact words from the user's message that ask for this "
                            "action, copied verbatim (for example 'make a doc')."
                        ),
                    },
                    "title": {"type": "string", "description": "The Doc's title."},
                    "body": {"type": "string", "description": "The Doc's text."},
                },
                "required": ["request_words", "title", "body"],
            },
        },
    }

    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        body = clean_body(args.get("body"), ctx.question, [s.uri for s in ctx.sources if s.uri])
        if not body:
            raise ActionRejected("The draft is empty.")
        title = clean_title(args.get("title"))
        return Proposal(
            action=self.name,
            target=ActionTarget("google_drive", "my-drive", "Your Google Drive (only you can see it)"),
            payload={"title": title, "body": without_title_heading(body, title)},
            audience=Audience(members=()),  # nobody but the asker
        )

    def execute(self, key, target, payload, token) -> ActionResult:
        boundary = f"handbook-{uuid.uuid4().hex}"
        metadata = {"name": payload["title"], "mimeType": "application/vnd.google-apps.document"}
        content = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            # Markdown, which Google converts into Doc headings, lists and
            # bold; as text/plain the Doc showed the asterisks and hashes.
            f"--{boundary}\r\nContent-Type: text/markdown; charset=UTF-8\r\n\r\n"
            f"{payload['body']}\r\n--{boundary}--\r\n"
        ).encode()
        try:
            response = httpx.post(
                UPLOAD_URL,
                params={"uploadType": "multipart", "fields": "id,name,webViewLink"},
                content=content,
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": f"multipart/related; boundary={boundary}"},
                timeout=_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise ActionOutcomeUnknown("Google did not answer.", cause=exc) from exc
        if response.status_code == 401:
            raise OAuthReauthRequiredError("Reconnect your Google account.")
        if response.status_code >= 500:
            raise ActionOutcomeUnknown(f"Google answered {response.status_code}.")
        if response.status_code >= 400:
            raise ActionError(f"Google refused to create the Doc ({response.status_code}).")
        made = response.json() or {}
        if not made.get("id"):
            raise ActionError("Google did not create the Doc.")
        return ActionResult(made["id"], made.get("webViewLink"))
