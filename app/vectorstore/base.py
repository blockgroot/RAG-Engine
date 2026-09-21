"""The vector store contract the rest of the app depends on.

This is the ONLY surface the application uses to persist and retrieve document
chunks. It never touches Postgres or pgvector specifics directly — those live in
concrete implementations (see ``pgvector_store.py``), so the backing store can be
swapped without changing callers.

Multi-tenant isolation is baked into the contract: every method requires an
``org_id``. There is no way to insert or query without naming the tenant.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DateRange:
    """Hard filter on ``documents.source_last_modified`` for retrieval.

    Either bound may be ``None`` (open-ended). This is a hard filter (like
    ``org_id``/``source_provider``), applied in the ``WHERE`` clause before
    ranking — never a re-ranking signal. Documents with no
    ``source_last_modified`` (e.g. a manual/plain ingest) never match a
    bounded range, since there is no date to compare against.
    """

    after: datetime | None = None
    before: datetime | None = None


@dataclass(frozen=True)
class Viewer:
    """WHO is asking, for document-level access filtering.

    ``org_id`` says which tenant may read a row and ``workspace_id`` which
    space; this says which PERSON. It exists because the smallest private unit
    used to be a space: we sync with one admin's token, so a Drive file shared
    with two people became readable by everyone in the scope that indexed it.

    ``email`` is the address the asker signed in with, matched against
    ``documents.doc_viewers`` (lowercased) -- the source's own sharing list,
    captured at sync. ``None`` means UNRESTRICTED: no document filter at all,
    exactly the behaviour before this existed. That is the right default for
    ingestion, evaluation and CLI reads, and the wrong one for a member's
    question, so every production read path passes a real one and
    ``tests/test_doc_access.py`` pins that it does.

    A person is matched by EMAIL rather than ``users.id`` because a file is
    routinely shared with someone who has not signed up yet -- the entry starts
    matching the day they log in, with nothing to reconcile.

    ``groups`` carries the same person's Google Group memberships, which is how
    a ``group:`` grant is satisfied. Build a viewer for a real person through
    ``sources.google_groups.viewer_for_person`` rather than constructing one
    here, so no call site can silently drop them.
    """

    email: str | None = None
    #: Google Group addresses this person belongs to, lowercased and WITHOUT
    #: the ``group:`` prefix (``acl`` adds it). Drive reports a group share as
    #: one opaque ``group:<address>`` grant and never expands it, so without
    #: this a file shared only with ``engineering@corp.com`` matches nobody.
    #:
    #: Expanded on the READ side -- the asker's own groups -- rather than at
    #: ingest, exactly as ``domain:`` already is. The stored grant stays what
    #: Drive actually said, it is one directory call per PERSON instead of one
    #: per group, and a membership change takes effect with no re-stamp and no
    #: re-embed. Expanding at ingest would reintroduce the revocation problem
    #: ``_restamp_unchanged_access`` exists to solve, except worse: a group
    #: edit moves no Drive metadata at all, so nothing would signal it.
    #:
    #: Empty is the fail-closed default and means exactly what shipped before
    #: group support: a group-shared document is withheld. See
    #: ``sources.google_groups``.
    groups: tuple[str, ...] = ()
    #: Restrict to scope-public documents with no person attached. Needed
    #: because "no email" has TWO meanings that must not share a value: an
    #: internal caller with no filter at all, and a caller who may read only
    #: what the whole scope may read. A bare `Viewer(email=None)` is the first;
    #: this flag is the second, and it is what a Slack CHANNEL reply and an
    #: unresolvable identity both get.
    public_only: bool = False

    @classmethod
    def unrestricted(cls) -> "Viewer":
        """No document-level filter. Spelled out so a call site is greppable."""
        return cls(email=None)

    @classmethod
    def public_only_viewer(cls) -> "Viewer":
        """Scope-public documents only — nobody's private documents, ever."""
        return cls(email=None, public_only=True)

    @property
    def is_unrestricted(self) -> bool:
        return not self.public_only and not (self.email or "").strip()

    def acl(self) -> list[str]:
        """The ACL entries this person satisfies, formatted as stored.

        One formatter for both sides -- the write path (``sources``) and this
        read path MUST agree on spelling, or a correct grant silently stops
        matching. Lowercased, plus ``domain:<host>`` for Drive's domain-wide
        shares.

        ponytail: the domain entry is INFERRED from the address, not read from
        a directory. Onyx populates its domain group from the real Workspace
        roster for exactly this reason; upgrade when we have the Admin SDK.
        """
        email = (self.email or "").strip().lower()
        if not email:
            # An empty ACL is not "match everything": `doc_viewers && '{}'` is
            # false for every row, so the predicate reduces to `doc_is_public`.
            # Groups are deliberately ignored here too: a viewer with no
            # identity has no memberships to honour, so `public_only` cannot be
            # widened by handing it a group list.
            return []
        entries = [email]
        if "@" in email:
            entries.append(f"domain:{email.split('@', 1)[1]}")
        # Spelled to match `google_drive._file_access`, which is the only
        # writer of a `group:` entry. Deduplicated because a directory that
        # lists the same group twice must not change the query's meaning.
        seen = set(entries)
        for group in self.groups:
            entry = f"group:{group.strip().lower()}"
            if group.strip() and entry not in seen:
                seen.add(entry)
                entries.append(entry)
        return entries


@dataclass(frozen=True)
class RestrictedMatch:
    """What a viewer filter WITHHELD from one query, for the refusal message.

    Read only after a question has already failed the confidence gate, and
    deliberately carries no content, no title and no document id: naming the
    document that matched would tell the asker what is in a file they are not
    allowed to open, which is the leak this whole feature exists to close.
    ``source_provider`` is safe because the connected source (and its Drive
    folder name) is already on the space page for every member.
    """

    score: float
    source_provider: str | None


@dataclass(frozen=True)
class RetrievedChunk:
    """A single search hit returned from the store."""

    content: str
    score: float  # cosine similarity in [0, 1]; higher is more similar
    document_id: str
    chunk_index: int
    org_id: str
    # Human title from ``documents.title`` when the store JOINed it (preferred
    # for citation UI). Optional so fakes / reuse paths stay lightweight.
    document_title: str | None = None
    # Slack recap: map ``"{channel_id}:{thread_ts}"`` back to a channel name
    # when the stored title was ingested before titles carried a ``#channel:`` prefix.
    source_external_id: str | None = None
    # Provenance, for the retrieved-context header the grounded prompt builds.
    # "Who wrote this?", "when was it last updated?" and "which app is this
    # from?" are among the most common things anyone asks about a document, and
    # every answer is already sitting on the `documents` row this hit JOINed --
    # it was simply dropped before reaching the model. All optional so fakes,
    # the reuse path and legacy rows stay lightweight.
    source_provider: str | None = None
    last_editor: str | None = None
    last_modified: datetime | None = None
    # Whether this hit's document is readable by the whole scope. Carried so
    # the answer cache can tell a shareable answer from a personal one: an
    # answer built from a restricted document must never be served to the next
    # member who asks the same question (`rag/query_cache.py` has no viewer in
    # its key, deliberately -- so the write is gated instead). Defaults True,
    # which is what a fake, a reuse hit or a legacy row honestly is.
    doc_is_public: bool = True


@dataclass(frozen=True)
class OrganizationRef:
    """A tenant, for listing/selection (e.g. the CLI org picker, Phase 9)."""

    id: str
    name: str
    document_count: int = 0


@dataclass(frozen=True)
class StoredSourceDocument:
    """Sync metadata for one ingested source page (incremental re-sync).

    ``provider`` (e.g. ``"notion"``, ``"google"``) partitions sync state so a
    sync for one provider never diffs against another provider's rows in the
    same org — see CLAUDE.md §4 / GOOGLE_INTEGRATION_PLAN.md §3.
    """

    document_id: str
    provider: str
    external_id: str
    title: str
    source_uri: str | None
    last_modified: datetime | None


class VectorStore(ABC):
    """Abstract, tenant-scoped store for document chunks and their embeddings."""

    @abstractmethod
    def create_organization(self, name: str) -> str:
        """Create a tenant and return its ``org_id``."""
        raise NotImplementedError

    def list_organizations(self) -> list["OrganizationRef"]:
        """List existing tenants (newest first), for selection UIs like the CLI.

        Optional capability: the default raises ``NotImplementedError``; stores
        that support it (``PgVectorStore``) override it. Not tenant-scoped — it is
        an operator-facing listing, not a per-tenant read.
        """
        raise NotImplementedError("this vector store does not support listing organizations")

    @abstractmethod
    def add_document(
        self,
        org_id: str,
        title: str,
        chunks: list[str],
        embeddings: list[list[float]],
        source_uri: str | None = None,
        workspace_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Store a document and its chunk embeddings; return the ``document_id``.

        ``chunks`` and ``embeddings`` must be the same length and aligned by
        index. All rows are written under ``org_id``.

        ``workspace_id`` (Workspace-within-a-Workspace): ``None`` (default)
        stores an org-wide row, identical to every existing call site.
        Non-``None`` scopes the row to that sub-workspace — it is written
        alongside ``org_id``, never instead of it.

        ``tags``: an arbitrary, caller-supplied label list (e.g. a
        department) for the hard tag filter on ``query``/``keyword_search``.
        ``None``/empty (the default) means untagged — this store has no
        opinion on where a tag comes from.
        """
        raise NotImplementedError

    @abstractmethod
    def query(
        self,
        org_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        workspace_id: str | None = None,
        source_provider: str | None = None,
        date_range: "DateRange | None" = None,
        tags: list[str] | None = None,
        viewer: "Viewer | None" = None,
    ) -> list[RetrievedChunk]:
        """Return the ``top_k`` most similar chunks *within ``org_id`` only*.

        ``viewer`` (document-level access filtering): ``None`` (default) reads
        every document in scope, exactly as before this existed. A ``Viewer``
        with an email additionally requires each hit's document to be
        scope-public OR to name that person in ``documents.doc_viewers``.
        Applied in the ``WHERE`` clause BEFORE ranking, for the same reason
        ``org_id`` is: a filter that runs after scoring is a filter the index
        can reorder around.

        ``date_range``: an optional hard filter on ``documents.source_last_modified``
        (e.g. "only policies updated in the last quarter"). ``None`` (default)
        is a no-op, identical to every existing call site. Applied in the
        ``WHERE`` clause before ranking, same as ``source_provider`` — never a
        re-ranking signal, and it never widens what ``org_id``/``workspace_id``
        already restrict.

        ``tags``: an optional hard filter on ``documents.tags`` (e.g.
        department/category labels set at ingest). ``None``/empty (default)
        is a no-op. When set, matches a document whose ``tags`` overlaps
        ANY of the given values (OR semantics) — same WHERE-clause-before-
        ranking discipline as ``date_range``, never a re-ranking signal.

        ``workspace_id`` (Workspace-within-a-Workspace): ``None`` (default)
        queries only org-wide chunks (rows with ``workspace_id IS NULL``) —
        every existing call site is unaffected. A non-``None`` value queries
        only that sub-workspace's chunks and NEVER also the org-wide ones —
        a sub-workspace's answers must never silently blend in the parent
        org's policy content (see CLAUDE.md's Workspace-within-a-Workspace
        plan §0.3 for the reasoning). Always paired with ``org_id`` — never
        resolved from ``workspace_id`` alone.

        ``source_provider`` (Slack Agent): ``None`` (default) queries every
        provider's chunks exactly as before. A value (e.g. ``"slack"``)
        restricts the search to chunks whose ``documents.source_provider``
        matches — this is what lets a Slack-only agent answer *only* from
        chat threads rather than silently citing a Notion page. Unlike
        ``workspace_id`` this is NOT an access boundary (every provider in
        scope is already inside the caller's ``org_id``/``workspace_id``);
        it is a relevance/pertinence filter, so it is pinned per-agent at
        construction rather than accepted per request.
        """
        raise NotImplementedError

    def list_chunk_texts(self, org_id: str) -> list[str]:
        """Return raw chunk ``content`` strings for ``org_id`` (Phase 17 vocab).

        Used to build a per-tenant SymSpell dictionary for query spelling
        correction. Optional: default raises; ``PgVectorStore`` implements it.
        """
        raise NotImplementedError("this vector store does not support listing chunk texts")

    def keyword_search(
        self,
        org_id: str,
        query_text: str,
        query_embedding: list[float],
        top_k: int = 30,
        workspace_id: str | None = None,
        source_provider: str | None = None,
        date_range: "DateRange | None" = None,
        tags: list[str] | None = None,
        viewer: "Viewer | None" = None,
    ) -> list[RetrievedChunk]:
        """Full-text (BM25-style) search within ``org_id``, ordered by keyword
        relevance (Phase 6 hybrid retrieval).

        ``date_range`` behaves exactly as on ``query`` — the keyword half of
        hybrid search must apply the same filter, or a date-scoped answer
        could still surface an out-of-range chunk through the BM25 leg.

        ``source_provider`` behaves exactly as on ``query`` — the keyword half
        of hybrid search must apply the same filter, or a Slack-scoped answer
        could still surface a Notion chunk through the BM25 leg.

        ``tags`` behaves exactly as on ``query`` — same overlap-match
        semantics, applied to the same ``documents.tags`` column.

        ``viewer`` behaves exactly as on ``query`` — the keyword half of hybrid
        search must apply the same access filter, or a document the asker
        cannot open would still reach them through the BM25 leg. It is applied
        inside the candidate CTE, i.e. before BM25 re-ranks in Python.

        Optional capability: the default raises ``NotImplementedError``; stores
        that support it (``PgVectorStore``) override it. Each returned chunk still
        carries its cosine ``score`` (computed against ``query_embedding``) so a
        keyword-only hit can flow through the same confidence gate as a vector hit.
        """
        raise NotImplementedError("this vector store does not support keyword search")

    def recent_chunks(
        self,
        org_id: str,
        provider: str,
        *,
        workspace_id: str | None = None,
        limit: int = 40,
    ) -> list[RetrievedChunk]:
        """Most RECENTLY updated chunks for one provider — ordered by time, not similarity.

        Every other read here ranks by relevance to a question. That is the
        wrong selection for a "catch me up on the last few days" request: no
        individual thread resembles that sentence, so similarity search returns
        arbitrary chunks and the grounded prompt correctly refuses. Recency is
        the selection such a question actually implies.

        Scoped exactly like ``query`` — ``org_id`` plus ``workspace_id`` are
        still the isolation guarantees, and ``provider`` the relevance filter.
        Chunks come back with ``score = 0.0``: there is no query vector, and
        inventing a similarity here would feed a meaningless number to a
        confidence gate calibrated for cosine.

        Optional capability: the default raises ``NotImplementedError``.
        """
        raise NotImplementedError("this vector store does not support recency retrieval")

    def restricted_match(
        self,
        org_id: str,
        query_embedding: list[float],
        *,
        workspace_id: str | None = None,
        source_provider: str | None = None,
        viewer: "Viewer | None" = None,
        min_score: float = 0.0,
    ) -> "RestrictedMatch | None":
        """Did the ``viewer`` filter WITHHOLD something this question wanted?

        Answers one question and only one: is there a chunk in scope that
        scores at least ``min_score`` and that this person may NOT read. Used
        exclusively on the refusal path, to tell a member "this space has
        documents you have not been given access to" instead of "I don't know"
        — the second is indistinguishable from "nobody wrote that down", and
        sends them to write a document that already exists.

        It runs as a SECOND query rather than by relaxing the first, because
        the alternative is fetching rows the asker cannot read and filtering
        them in Python: then one bug in that filter is a content leak, whereas
        here the restricted rows never leave the database. Nothing it returns
        reaches the prompt.

        Costs one extra query on refusals only, never on an answered question.
        Optional capability: the default returns ``None`` (never claim a
        withheld document we did not verify).
        """
        return None

    def list_source_documents(
        self, org_id: str, provider: str, workspace_id: str | None = None
    ) -> list["StoredSourceDocument"]:
        """Return ingested source-page metadata for incremental sync.

        Scoped to ``provider`` (e.g. ``"notion"``, ``"google"``) as well as
        ``org_id`` — sync state for one provider must never be diffed against
        another provider's rows in the same org (see CLAUDE.md §4). Optional:
        default raises. Rows without ``source_external_id`` are omitted.
        """
        raise NotImplementedError("this vector store does not support source document listing")

    def upsert_source_document(
        self,
        org_id: str,
        *,
        provider: str,
        external_id: str,
        title: str,
        chunks: list[str],
        embeddings: list[list[float]],
        source_uri: str | None = None,
        last_modified: datetime | None = None,
        workspace_id: str | None = None,
        tags: list[str] | None = None,
        last_editor: str | None = None,
        is_public: bool = True,
        viewers: list[str] | None = None,
    ) -> str:
        """Replace any prior copy of this source page, then store the new chunks.

        ``is_public``/``viewers`` are the document's access set as the SOURCE
        reports it (see ``sources.base.DocAccess``). ``True``/``None`` — the
        default — means "readable by the whole scope", which is what every
        provider that cannot report an ACL keeps.

        Deletes existing rows for the same ``(org_id, provider, external_id)``
        and any legacy duplicates that share ``source_uri`` (within the same
        provider) but lack an external id, then inserts one fresh document.
        Optional capability. ``tags`` behaves exactly as on ``add_document``.
        """
        raise NotImplementedError("this vector store does not support source document upsert")

    def acknowledge_source_document(
        self,
        org_id: str,
        *,
        provider: str,
        external_id: str,
        title: str,
        source_uri: str | None = None,
        last_modified: datetime | None = None,
        workspace_id: str | None = None,
        tags: list[str] | None = None,
        last_editor: str | None = None,
        is_public: bool = True,
        viewers: list[str] | None = None,
    ) -> str:
        """Record a source page with no chunks (empty / index-only after fetch).

        Keeps change detection from reporting the same empty page as "new" forever.
        Optional capability.
        """
        raise NotImplementedError(
            "this vector store does not support source document acknowledge"
        )

    def set_source_document_access(
        self,
        org_id: str,
        *,
        provider: str,
        entries: list[tuple[str, bool, list[str] | None]],
        workspace_id: str | None = None,
    ) -> int:
        """Re-stamp the access set of already-stored documents. Returns rows touched.

        This is what makes REVOCATION work. A permission change moves no
        content and no ``modifiedTime``, so an unshared file is classed
        "unchanged" by ``ingestion.pipeline._plan_refs`` and is never
        re-fetched — its ACL would otherwise stay as it was on the day it was
        first indexed, forever. Drive reports sharing in the LISTING we already
        make every sync, so the new set is in hand with no extra call.

        Each entry is ``(external_id, is_public, viewers)`` and REPLACES the
        stored set, never unions with it: a union can only ever add viewers,
        which makes removal impossible to express.

        Optional capability: the default is a no-op.
        """
        return 0

    def delete_source_documents(
        self,
        org_id: str,
        provider: str,
        external_ids: list[str],
        workspace_id: str | None = None,
    ) -> int:
        """Delete ingested pages by source external id, scoped to ``provider``.

        Returns rows removed. ``workspace_id`` scoping matters here too: a
        workspace's personal Notion connection and the org's admin Notion
        connection can both be ``provider="notion"``, so ``provider`` alone
        cannot disambiguate their documents — ``workspace_id`` closes that gap.
        """
        raise NotImplementedError("this vector store does not support source document delete")

    def delete_all_source_documents(
        self,
        org_id: str,
        provider: str,
        workspace_id: str | None = None,
    ) -> int:
        """Delete every ingested page for ``provider`` in this org/workspace scope.

        Used when Disconnecting a Notion/Drive connection or swapping a Drive
        folder so answers cannot keep citing revoked content. Chunks cascade.
        """
        raise NotImplementedError(
            "this vector store does not support bulk source document delete"
        )
