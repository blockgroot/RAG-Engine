"""The Act-mode action interface.

The model never calls an API: it fills ONE tool schema, ``prepare`` freezes it,
the person approves, and only then ``execute`` runs from the STORED row with
their token. No method both decides and writes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..core.exceptions import ActionRejected
from .draft import clean_body, clean_title


@dataclass(frozen=True)
class ActionTarget:
    """Where a write goes, resolved by code, never typed by the model. ``url`` comes
    from the tool's own API."""

    kind: str
    external_id: str
    name: str
    url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "external_id": self.external_id,
                "name": self.name, "url": self.url}


@dataclass(frozen=True)
class Source:
    """A document the draft drew on, with who may read it (lowercased emails, as
    ``documents.doc_viewers``). Only hits the asker was shown become sources."""

    document_id: str
    title: str
    public: bool
    viewers: tuple[str, ...] = ()
    uri: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"document_id": self.document_id, "title": self.title,
                "public": self.public, "viewers": len(self.viewers)}


@dataclass(frozen=True)
class Audience:
    """Who will read the write once it lands. ``members`` is ``None`` when unknown
    or cut short: an unknown audience is never treated as a small one."""

    public: bool = False
    members: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Proposal:
    """One write, exactly as it will be sent. Nothing about it changes after
    the person sees it: the stored copy is what ``execute`` receives."""

    action: str
    target: ActionTarget
    payload: dict[str, Any]
    audience: Audience = field(default_factory=Audience)


def details(*pairs: tuple[str, str | None]) -> list[list[str]]:
    """Settings a write carries besides its words, as ``[label, value]`` card rows.
    In the stored payload (fingerprinted); never editable."""
    return [[label, value] for label, value in pairs if value]


@dataclass(frozen=True)
class ActionResult:
    """What the tool created: its id and an http(s) link from its API."""

    external_id: str
    url: str | None = None
    #: What the tool calls the record ("Final E2E notes"), when it says.
    name: str | None = None


@dataclass(frozen=True)
class ActionContext:
    """Everything ``prepare`` may use, all from the session or code. ``question`` is
    the only text a target, mention or link may come from; ``token`` is used only
    to READ.
    """

    org_id: str
    workspace_id: str | None
    user_id: str
    email: str
    question: str
    token: str
    sources: tuple[Source, ...] = ()
    #: The asker's ``Viewer``: a target that is in our index must be one they
    #: may read, whatever the tool itself would allow.
    viewer: Any = None
    #: Where the request was made (web, slack_dm, slack_channel + ids); never from the model.
    surface: dict | None = None
    #: What lookups returned while drafting, so an id taken from one is grounded.
    lookups: str = ""


class Action(ABC):
    """One kind of write in one tool.

    ``tool`` is the JSON schema the model fills in. ``destructive`` is labelled and
    its card says it cannot be undone (``mcp_store.destructive``, by name).
    """

    name: ClassVar[str]
    provider: ClassVar[str]
    label: ClassVar[str]
    tool: ClassVar[dict[str, Any]]
    destructive: ClassVar[bool] = False
    #: A read run while drafting (an MCP lookup), never a card or a write.
    lookup: ClassVar[bool] = False
    #: Whose name the tool will show on the result, said on the card. "as you"
    #: only where the tool records the person; a bot-recorded write says so.
    attribution: ClassVar[str] = "as you"

    def attribution_for(self, payload: dict[str, Any]) -> str:
        """Whose name the result carries, for this draft."""
        return self.attribution

    def enabled_for(self, org_id: str, workspace_id: str | None) -> bool:
        """Switched on here right now (checked again when the write is sent)."""
        from . import policy

        return policy.is_enabled(org_id, workspace_id, self.name)

    def token_for(self, org_id: str, user_id: str, surface: str = "web",
                  payload: dict[str, Any] | None = None) -> str:
        """The asker's own write token, refreshed. ``surface``: where it was asked;
        ``payload``: the stored draft."""
        from ..auth.user_connections import fresh_user_token

        return fresh_user_token(org_id, user_id, self.provider)

    #: Payload fields the person may rewrite: drafted words only, never the target.
    editable_fields: ClassVar[tuple[str, ...]] = ("title", "body")

    def editable(self, payload: dict[str, Any]) -> dict[str, str]:
        """The drafted words as the person reads and edits them."""
        return {k: payload[k] for k in self.editable_fields if k in payload}

    def edit(self, payload: dict[str, Any], fields: dict[str, Any], *,
             asked: str | None = None) -> dict[str, Any]:
        """The stored payload with the edit applied, cleaned like a draft. A person's
        edit keeps its own links and mentions (``asked`` None); a model rewrite passes
        ``asked`` so it cannot add any."""
        current = self.editable(payload)
        if not fields or set(fields) - set(current):
            raise ActionRejected(
                "Only the drafted text can be edited. To change where it goes, cancel and ask again."
            )
        new = dict(payload)
        for key, value in fields.items():
            text = value if isinstance(value, str) else ""
            new[key] = clean_title(text) if key == "title" else clean_body(
                text, text if asked is None else asked, ())
        if "title" not in new and not new.get("body"):
            raise ActionRejected("The draft is empty.")
        return new

    @abstractmethod
    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        """Validate the model's arguments and freeze the write.

        Raises ``ActionRejected`` when anything is off. Never writes.
        """

    @abstractmethod
    def execute(
        self, key: str, target: dict[str, Any], payload: dict[str, Any], token: str
    ) -> ActionResult:
        """Send the stored write once, with the asker's token.

        ``key`` (the ``actions`` row id) goes to tools that accept a client id, so a
        repeat cannot create a second object. Raises ``ActionOutcomeUnknown`` when sent
        with no answer, ``ActionError`` when refused.
        """
