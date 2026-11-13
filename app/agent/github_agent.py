"""The GitHub agent: answers repository questions from live API reads only.

Not a RAG agent (nothing is embedded), so grounding is structural:

1. An answer is only ever composed from tool output. No tool, a failed
   tool or unparseable arguments return the fixed fallback.
2. ``build_github_answer_prompt`` forbids filling in from world knowledge.

One tool round, not a loop, so latency and cost stay bounded. Tool output
is untrusted (any contributor writes a README) and is fenced and scrubbed
like retrieved chunks.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator

from ..config.settings import GitHubAgentSettings, SecuritySettings
from ..githublive.access import restrict, restricted_message
from ..guard.moderation import answer_is_unsafe
from ..security.links import enforce_link_provenance
from ..security.untrusted import leaks_canary
from ..core.answer_sources import (
    RECOVERY_REASON_INSUFFICIENT_EVIDENCE,
    SOURCE_GITHUB,
    SOURCE_NONE,
)
from ..core.exceptions import ConfigurationError, LLMProviderError, ProviderError, SourceError
from ..llm.routed import default_model_only
from ..core.streaming import chunk_answer
from ..githublive.base import (
    CommitDetail,
    CommitSummary,
    GitHubReader,
    RepoReadme,
    dedupe_reviews,
)
from ..llm.base import LLMProvider
from ..rag.prompts import (
    GITHUB_TOOLS,
    build_github_answer_prompt,
    build_github_decision_prompt,
    format_repo_catalog,
)
from ..vectorstore.base import Viewer
from .base import Agent, AgentResponse, Citation

logger = logging.getLogger(__name__)

ReaderBuilder = Callable[..., GitHubReader]

# Parses the "MODE: A|B|C\n\n<answer>" tag, same shape as ``RagPipeline``'s.
_MODE_TAG_RE = re.compile(r"^\s*MODE:\s*([ABC])\s*\n+(.*)", re.IGNORECASE | re.DOTALL)


def cited_links(citations: list[Citation]) -> list[dict]:
    """Sources under the answer. The address is the one GitHub returned."""
    out: list[dict] = []
    seen: set[str] = set()
    for c in citations:
        url = (c.url or "").strip()
        if not url.startswith(("https://", "http://")) or url in seen:
            continue
        seen.add(url)
        ref = c.reference
        if ref.endswith("#readme"):
            title = f"{ref[:-7]} README"
        elif ref.endswith("#about"):
            title = ref[:-6]
        elif c.content and len(c.content) <= 80 and "\n" not in c.content:
            title = c.content
        else:
            title = ref
        out.append({
            "n": len(out) + 1,
            "document_id": ref,
            "title": title,
            "provider": "github",
            "url": url,
        })
    return out


def _citations_are_about_only(citations: list[Citation]) -> bool:
    """True when every citation is catalog metadata (``repo#about``), no README/commits."""
    if not citations:
        return False
    return all((c.reference or "").lower().endswith("#about") for c in citations)


def _split_mode_tag(raw: str) -> tuple[str | None, str]:
    """Split a declared mode off a generation. Untagged returns ``(None, raw)``,
    treated as sufficient (see ``_compose``)."""
    match = _MODE_TAG_RE.match(raw or "")
    if not match:
        return None, (raw or "").strip()
    return match.group(1).upper(), match.group(2).strip()


class GitHubAgent(Agent):
    """Answers questions about an org's authorized GitHub repositories."""

    def __init__(
        self,
        llm: LLMProvider,
        reader_builder: ReaderBuilder,
        fallback_response: str,
        settings: GitHubAgentSettings | None = None,
    ) -> None:
        """Build the agent.

        ``reader_builder`` is injected for testability, and is a builder because a
        reader is tenant-scoped: built per request so the agent never holds one
        tenant's credentials.
        """
        self._llm = llm
        self._build_reader = reader_builder
        self._fallback = fallback_response
        self._settings = settings or GitHubAgentSettings.from_env()
        self._link_allowlist = SecuritySettings.from_env().link_allowlist

    def answer(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        viewer: Viewer | None = None,
    ) -> AgentResponse:
        # No GitHub connection costs zero LLM calls.
        try:
            reader = self._build_reader(org_id, workspace_id)
        except (ConfigurationError, ProviderError):
            return self._fallback_response()

        # The installation token reads every granted repo; narrow to those the ASKER
        # can open (`githublive.access`).
        reader = restrict(reader, org_id, viewer)
        try:
            repos = reader.list_repos()
        except ProviderError:
            return self._fallback_response()
        withheld = restricted_message(reader)
        if withheld:
            return AgentResponse(
                answer=withheld, grounded=False, source=SOURCE_NONE, citations=[],
                access_restricted=True,
            )

        decision = self._decide_tool(question, repos)
        if decision is None:
            return self._fallback_response()

        evidence = self._run_tool(reader, decision, known_repos=repos)
        if evidence is None:
            return self._fallback_response()

        evidence_block, citations = evidence

        composed = self._compose(question, evidence_block)
        if composed is None:
            return self._fallback_response()
        mode, answer = composed

        # Mode C = nothing relevant, B = partial. For get_readme questions try one
        # supplementary commit fetch on B or C (a template README is often labelled
        # B); if it fails keep B, fall back on C. Also on A when the only grounding
        # is catalog `#about` metadata, which is true but thin.
        thin_about_only = mode == "A" and _citations_are_about_only(citations)
        if (
            self._settings.evidence_recovery_enabled
            and (mode in ("B", "C") or thin_about_only)
        ):
            recovered = self._recover(
                question, reader, decision, evidence_block, citations
            )
            if recovered is not None:
                return recovered

        if mode == "C":
            return self._fallback_response()

        return AgentResponse(
            answer=answer,
            grounded=True,
            source=SOURCE_GITHUB,
            citations=citations,
            cited=cited_links(citations),
            response_mode=mode,
        )

    def _compose(self, question: str, evidence_block: str) -> tuple[str | None, str] | None:
        """One generation over the evidence. ``None`` on failure/empty."""
        try:
            raw = self._llm.generate(
                build_github_answer_prompt(question, evidence_block)
            )
        except LLMProviderError:
            return None
        mode, answer = _split_mode_tag(raw)
        if answer and leaks_canary(answer):
            logger.warning("security.canary_leak stage=github")
            return None
        if answer:
            answer = enforce_link_provenance(answer, [evidence_block], self._link_allowlist)
        if not answer or answer_is_unsafe(answer, org_id=None, stage="github"):
            return None
        return mode, answer

    def _recover(
        self,
        question: str,
        reader: GitHubReader,
        decision: tuple[str, dict],
        evidence_block: str,
        citations: list[Citation],
    ) -> AgentResponse | None:
        """ONE supplementary evidence round after a thin Mode B/C answer.

        Recent commit subjects describe a project whose README is a template. At
        most one extra round, and no second generation without new evidence.
        ``None`` when it could not help.
        """
        name, arguments = decision
        repo = arguments.get("repo")
        if not isinstance(repo, str) or not repo:
            return None
        # Only the repo-overview path benefits from more commits.
        if name != "get_readme":
            return None

        try:
            commits = reader.list_commits(
                repo, limit=self._settings.recovery_commit_count
            )
        except (SourceError, ProviderError, ValueError, TypeError):
            return None
        if not commits:
            return None

        supplement = self._format_commits(commits)
        if supplement is None:
            return None
        commit_block, commit_citations = supplement

        combined = (
            f"{evidence_block}\n\n"
            "--- additional evidence: recent commit history ---\n"
            f"{commit_block}"
        )
        composed = self._compose(question, combined)
        if composed is None:
            return None
        mode, answer = composed
        if mode == "C":
            # Second look, still nothing: don't dress it up.
            return None

        recovered_citations = list(citations) + list(commit_citations)
        return AgentResponse(
            answer=answer,
            grounded=True,
            source=SOURCE_GITHUB,
            citations=recovered_citations,
            cited=cited_links(recovered_citations),
            response_mode=mode,
            recovery_used=True,
            recovery_reason=RECOVERY_REASON_INSUFFICIENT_EVIDENCE,
        )

    def answer_stream(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        viewer: Viewer | None = None,
        chunk_chars: int = 40,
    ) -> tuple[Iterator[str], AgentResponse]:
        """Like ``answer``, but the text arrives as a chunk iterator.

        Chunks an already-decided answer: the tool decision can still resolve to
        the fallback, so raw tokens could leak discarded text.
        """
        result = self.answer(
            question,
            org_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            viewer=viewer,
        )

        return chunk_answer(result.answer, chunk_chars), result

    # -- internals ---------------------------------------------------------

    def read_evidence(
        self,
        question: str,
        org_id: str,
        *,
        workspace_id: str | None = None,
        viewer: Viewer | None = None,
    ) -> tuple[str, str, bool, str | None] | None:
        """What ``answer`` reads live, without writing an answer (for Act).

        ``(repo, evidence, public, url)``; ``public`` is True only when GitHub says
        so (unknown counts as private). ``None`` when there is nothing to read.
        """
        try:
            reader = restrict(self._build_reader(org_id, workspace_id), org_id, viewer)
            repos = reader.list_repos()
        except (ConfigurationError, ProviderError):
            return None
        if restricted_message(reader):
            return None
        decision = self._decide_tool(question, repos)
        if decision is None:
            return None
        evidence = self._run_tool(reader, decision, known_repos=repos)
        if evidence is None:
            return None
        block, citations = evidence
        repo = str(decision[1].get("repo") or "")
        ref = next((r for r in repos if r.full_name.lower() == repo.lower()
                    or r.full_name.lower().endswith("/" + repo.lower())), None)
        url = next((c.url for c in citations if (c.url or "").startswith("https://")), None)
        return (ref.full_name if ref else repo), block, bool(ref and ref.private is False), url

    def _fallback_response(self) -> AgentResponse:
        """The single place a GitHub refusal is built (``SOURCE_NONE``, like RAG)."""
        return AgentResponse(
            answer=self._fallback, grounded=False, source=SOURCE_NONE, citations=[]
        )

    def _decide_tool(self, question: str, repos) -> tuple[str, dict] | None:
        """One call: which tool, with which arguments? ``None`` means "no tool"."""
        prompt = build_github_decision_prompt(question, format_repo_catalog(repos))
        try:
            # Pinned to the configured model: choosing a tool is machinery, and an
            # unreliable tool-caller would turn every question into the fallback. The
            # composed answer (_compose) still follows the member's pick.
            with default_model_only():
                result = self._llm.generate_with_tools(
                    [{"role": "user", "content": prompt}],
                    tools=GITHUB_TOOLS,
                    tool_choice="auto",
                )
        except LLMProviderError:
            return None

        if not result.tool_calls:
            # The model judged the question not answerable from these repos.
            return None

        call = result.tool_calls[0]
        try:
            arguments = json.loads(call.arguments or "{}")
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(arguments, dict):
            return None
        return call.name, arguments

    def _run_tool(
        self,
        reader: GitHubReader,
        decision: tuple[str, dict],
        *,
        known_repos: list | None = None,
    ) -> tuple[str, list[Citation]] | None:
        """Execute exactly one read. Any failure returns ``None`` (-> fallback).

        A refused repo also degrades to the fallback, so a private name's
        existence is never confirmed. Exception: a README 404 on a catalogued repo
        is grounded on its stored description/topics.
        """
        name, arguments = decision
        repo = arguments.get("repo")
        if not isinstance(repo, str) or not repo:
            return None

        try:
            if name == "get_readme":
                return self._format_readme(
                    reader.get_readme(repo), known_repos=known_repos or []
                )
            if name == "get_commit":
                sha = arguments.get("sha")
                if not isinstance(sha, str) or not sha:
                    return None
                return self._format_commit(reader.get_commit(repo, sha))
            if name == "list_commits":
                path = arguments.get("path")
                limit = arguments.get("limit")
                return self._format_commits(
                    reader.list_commits(
                        repo,
                        path=path if isinstance(path, str) and path else None,
                        limit=int(limit) if isinstance(limit, (int, float)) else 10,
                    )
                )
            if name == "list_pull_requests":
                limit = arguments.get("limit")
                state = arguments.get("state")
                page = reader.list_pull_requests(
                    repo,
                    limit=int(limit) if isinstance(limit, (int, float)) else 20,
                    # Filtered on the request: filtering a newest-first window finds nothing on
                    # a busy repo.
                    state=state if isinstance(state, str) and state else "all",
                )
                return self._format_pull_requests(
                    _with_mergers(reader, page), state=state
                )
            if name == "list_reviews":
                number = _resolve_pull_number(reader, repo, arguments)
                if number is None:
                    return None
                return self._format_reviews(
                    repo, number, reader.list_reviews(repo, number)
                )
            if name == "list_branches":
                limit = arguments.get("limit")
                return self._format_branches(
                    reader.list_branches(
                        repo,
                        limit=int(limit) if isinstance(limit, (int, float)) else 50,
                    )
                )
        except (SourceError, ProviderError, ValueError, TypeError):
            if name == "get_readme":
                meta = self._format_repo_metadata(repo, known_repos or [])
                if meta is not None:
                    return meta
            return None

        # A hallucinated tool name is just no evidence.
        return None

    @staticmethod
    def _format_repo_metadata(
        repo: str, known_repos: list
    ) -> tuple[str, list[Citation]] | None:
        """Ground a README miss on the installation catalog only, so a coaxed
        foreign name still falls back."""
        candidate = (repo or "").strip().lower()
        if not candidate:
            return None

        matched = None
        for item in known_repos:
            full = (getattr(item, "full_name", "") or "").lower()
            if not full:
                continue
            bare = full.rsplit("/", 1)[-1]
            if candidate in {full, bare}:
                matched = item
                break
        if matched is None:
            return None

        description = (getattr(matched, "description", None) or "").strip()
        topics = tuple(getattr(matched, "topics", ()) or ())
        if not description and not topics:
            return None

        lines = [
            f"Repository about for {matched.full_name}",
            "(no README is available — this is the description/topics recorded "
            "for this GitHub installation, not a project README)",
            "",
            f"Name: {matched.full_name}",
        ]
        if description:
            lines.append(f"Description: {description}")
        if topics:
            lines.append("Topics / tech tags: " + ", ".join(topics))
        # No guidance line here: this block is UNTRUSTED evidence, and a trusted
        # instruction inside the fence teaches the model to obey the fence.
        block = "\n".join(lines)
        about_body = description or ("Topics: " + ", ".join(topics))
        return block, [
            Citation(
                content=about_body[:500],
                reference=f"{matched.full_name}#about",
                url=f"https://github.com/{matched.full_name}",
            )
        ]

    @classmethod
    def _format_readme(
        cls, readme: RepoReadme, *, known_repos: list | None = None
    ) -> tuple[str, list[Citation]]:
        """README plus the catalog's description/topics (a template README alone
        says little)."""
        header = f"README of {readme.repo}"
        if readme.truncated:
            header += " (truncated — only the beginning is shown)"
        parts = [f"{header}:\n\n{readme.content}"]
        citations = [
            Citation(content=readme.content[:500], reference=f"{readme.repo}#readme",
                     url=readme.url)
        ]
        meta = cls._format_repo_metadata(readme.repo, known_repos or [])
        if meta is not None:
            meta_block, meta_citations = meta
            parts.insert(0, meta_block)
            citations = list(meta_citations) + citations
        return "\n\n".join(parts), citations

    @staticmethod
    def _format_commit(commit: CommitDetail) -> tuple[str, list[Citation]]:
        lines = [
            f"Commit {commit.sha} in {commit.repo}",
            f"Author: {commit.author or 'unknown'}",
            f"Date: {commit.date.isoformat() if commit.date else 'unknown'}",
            f"Message:\n{commit.message}",
            f"Changes: +{commit.additions} -{commit.deletions} "
            f"across {commit.total_files} file(s)",
            "Files changed:",
        ]
        for file in commit.files:
            lines.append(
                f"- {file.path} ({file.status}, +{file.additions} -{file.deletions})"
            )
            if file.patch:
                lines.append(f"  diff:\n{file.patch}")
        if commit.files_truncated:
            lines.append(
                f"(only the first {len(commit.files)} of {commit.total_files} "
                "changed files are shown — truncated)"
            )
        block = "\n".join(lines)
        return block, [
            Citation(
                content=commit.message[:500],
                reference=f"{commit.repo}@{commit.sha}",
                url=commit.url,
            )
        ]

    @staticmethod
    def _format_pull_requests(
        page, *, state: str | None = None
    ) -> tuple[str, list[Citation]] | None:
        """Render pull requests with author, merger and reviewers as separate
        fields, so the model cannot merge them into one count."""
        items = list(getattr(page, "items", ()) or ())
        wanted = (state or "all").lower()
        if wanted == "open":
            items = [p for p in items if p.state == "open"]
        elif wanted == "merged":
            items = [p for p in items if p.state == "merged"]
        if not items:
            # No evidence: fall back rather than narrate an empty list.
            return None

        repo = items[0].repo
        lines = [f"Pull requests in {repo}:"]
        for pull in items:
            raised = pull.created_at.date().isoformat() if pull.created_at else "unknown date"
            bits = [f"raised {raised} by {pull.author or 'unknown'}"]
            if pull.state == "merged":
                merged = pull.merged_at.date().isoformat() if pull.merged_at else "unknown date"
                # The merger is OMITTED when unknown: GitHub leaves `merged_by` out of
                # listings, so "by unknown" would read as a claim.
                bits.append(
                    f"merged {merged} by {pull.merged_by}" if pull.merged_by
                    else f"merged {merged}"
                )
            else:
                bits.append(pull.state)
            lines.append(f"- #{pull.number} {pull.title} ({'; '.join(bits)})")

        if getattr(page, "truncated", False):
            # A newest-N list looking complete is the failure that matters: tell the model.
            lines.append(
                "(Only the most recent pull requests were read; there may be more.)"
            )

        citations = [
            Citation(content=p.title[:200], reference=f"{p.repo}#{p.number}", url=p.url)
            for p in items[:5]
        ]
        return "\n".join(lines), citations

    @staticmethod
    def _format_reviews(
        repo: str, pull_number: int, reviews: list
    ) -> tuple[str, list[Citation]] | None:
        """Who reviewed one pull request, one entry per person."""
        if not reviews:
            # "Nobody has reviewed it" is a real answer, so this does NOT fall back.
            return (
                f"Pull request #{pull_number} in {repo} has no reviews yet.",
                [Citation(content="no reviews", reference=f"{repo}#{pull_number}",
                          url=f"https://github.com/{repo}/pull/{pull_number}")],
            )

        # Keep each person's latest VERDICT: reviews come oldest-first, and
        # "commented, then approved" must count as approved.
        seen: dict[str, str] = {
            review.reviewer: (getattr(review, "state", "") or "reviewed")
            for review in dedupe_reviews(reviews)
        }

        lines = [f"Reviews on #{pull_number} in {repo}:"]
        for who, verdict in seen.items():
            lines.append(f"- {who}: {verdict.replace('_', ' ').lower()}")
        return "\n".join(lines), [
            Citation(content=", ".join(seen), reference=f"{repo}#{pull_number}",
                     url=f"https://github.com/{repo}/pull/{pull_number}")
        ]

    @staticmethod
    def _format_branches(branches: list) -> tuple[str, list[Citation]] | None:
        if not branches:
            return None
        repo = branches[0].repo
        lines = [f"Branches in {repo}:"]
        for branch in branches:
            lines.append(
                f"- {branch.name}" + (" (protected)" if branch.protected else "")
            )
        return "\n".join(lines), [
            Citation(
                content=", ".join(b.name for b in branches[:20]),
                reference=f"{repo} branches",
                url=f"https://github.com/{repo}",
            )
        ]

    @staticmethod
    def _format_commits(
        commits: list[CommitSummary],
    ) -> tuple[str, list[Citation]] | None:
        if not commits:
            # No commits: fall back rather than narrate an empty list.
            return None
        repo = commits[0].repo
        lines = [f"Recent commits in {repo}:"]
        for commit in commits:
            when = commit.date.date().isoformat() if commit.date else "unknown date"
            lines.append(
                f"- {commit.sha[:8]} ({when}, {commit.author or 'unknown'}): "
                f"{commit.message}"
            )
        citations = [
            Citation(content=c.message[:200], reference=f"{c.repo}@{c.sha}", url=c.url)
            for c in commits[:5]
        ]
        return "\n".join(lines), citations


#: A ceiling on detail CALLS, not list size, spent on the newest merges.
_MERGER_DETAIL_CALLS = 5


def _with_mergers(reader, page):
    """Fill ``merged_by`` on the newest merged pull requests in a list.

    Only the single-PR endpoint returns it, so the first few are enriched one
    call each and the rest omit the merger.
    """
    items = list(getattr(page, "items", ()) or ())
    detail = getattr(reader, "get_pull_request", None)
    if detail is None or not any(p.merged_at and not p.merged_by for p in items):
        return page

    budget = _MERGER_DETAIL_CALLS
    filled = []
    for pull in items:
        if budget and pull.merged_at and not pull.merged_by:
            budget -= 1
            try:
                full = detail(pull.repo, pull.number)
            except (SourceError, ProviderError, ValueError, TypeError):
                # One missing merger is a gap in one line, not a failed answer.
                full = None
            if full is not None:
                pull = full
        filled.append(pull)
    return type(page)(items=tuple(filled), truncated=getattr(page, "truncated", False))


#: The window a title match searches (newest first; old PRs fall back).
_PULL_MATCH_LIMIT = 30


def _resolve_pull_number(reader, repo: str, arguments: dict) -> int | None:
    """The pull request a review question is about, by number or by name.

    The agent runs ONE tool round, so the name is resolved here: two bounded
    reads inside one tool call.
    """
    number = arguments.get("pull_number")
    if isinstance(number, (int, float)) and int(number) > 0:
        return int(number)

    query = arguments.get("pull_query")
    if not isinstance(query, str) or not query.strip():
        return None

    try:
        page = reader.list_pull_requests(repo, limit=_PULL_MATCH_LIMIT)
    except (SourceError, ProviderError, ValueError, TypeError):
        return None

    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}
    if not words:
        return None

    best, best_score = None, 0
    for pull in getattr(page, "items", ()) or ():
        title = set(re.findall(r"[a-z0-9]+", (pull.title or "").lower()))
        score = len(words & title)
        # Strictly greater: two equal titles resolve to neither.
        if score > best_score:
            best, best_score = pull, score
        elif score == best_score and score > 0 and pull is not best:
            best = None
    return best.number if best is not None else None

