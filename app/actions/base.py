"""The Act-mode action interface (docs/plans/2026-10-13-write-actions.md).

The model never calls an API. It fills in ONE action's tool schema; code turns
that into a frozen ``Proposal`` (``prepare``), the person approves it, and only
then does ``execute`` run, with the person's own token, from the STORED row.
That split is the whole safety story, so it is the interface: there is no
method that both decides and writes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass(frozen=True)
class ActionTarget:
    """Where a write goes, resolved by code, never typed by the model.

    ``kind`` names what it is in the tool's terms ("linear_team",
    "github_issue"), ``name`` is what the card shows, ``url`` an http(s) link
    from the tool's own API.
    """

    kind: str
    external_id: str
    name: str
    url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "external_id": self.external_id,
                "name": self.name, "url": self.url}


@dataclass(frozen=True)
class Source:
    """A document the draft drew on, with who may read it.

    Only hits the asker was already shown become sources, so ``title`` is never
    something they could not see. ``viewers`` are lowercased emails, the same
    entries as ``documents.doc_viewers``.
    """

    document_id: str
    title: str
    public: bool
    viewers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"document_id": self.document_id, "title": self.title,
                "public": self.public, "viewers": len(self.viewers)}


@dataclass(frozen=True)
class Audience:
    """Who will be able to read the write once it lands.

    ``public`` is everyone in the company or the world. ``members`` is a full
    list of emails, or ``None`` when the tool cannot say or the list was cut
    short -- an unknown audience is never treated as a small one.
    """

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


@dataclass(frozen=True)
class ActionResult:
    """What the tool created: its id and an http(s) link from its API."""

    external_id: str
    url: str | None = None


@dataclass(frozen=True)
class ActionContext:
    """Everything ``prepare`` may use, all of it from the session or code.

    ``question`` is the asker's own words, the only text a target, a mention
    or a link may come from. ``token`` is the asker's own write token, used by
    ``prepare`` only to READ (resolve a target, list an audience).
    """

    org_id: str
    workspace_id: str | None
    user_id: str
    email: str
    question: str
    token: str
    sources: tuple[Source, ...] = ()


class Action(ABC):
    """One kind of write in one tool.

    ``tool`` is the JSON schema the model fills in; its target fields are
    handles from a list code built for this request, never ids or URLs.
    ``destructive`` actions (none in v1) will need a second confirmation.
    """

    name: ClassVar[str]
    provider: ClassVar[str]
    tool: ClassVar[dict[str, Any]]
    destructive: ClassVar[bool] = False

    @abstractmethod
    def prepare(self, args: dict[str, Any], ctx: ActionContext) -> Proposal:
        """Validate the model's arguments and freeze the write.

        Raises ``ActionRejected`` when anything is off. Never writes.
        """

    @abstractmethod
    def execute(self, target: dict[str, Any], payload: dict[str, Any], token: str) -> ActionResult:
        """Send the stored write once, with the asker's token.

        Raises ``ActionOutcomeUnknown`` when the request was sent and no answer
        came back, ``ActionError`` when the tool refused it.
        """
