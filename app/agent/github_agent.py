"""The GitHub agent: answers repository questions from live API reads only.

This is the second real backend behind the ``Agent`` contract, and the one
``app/agent/base.py`` has been predicting since Phase 7 ("a future GitHub agent
will implement the *same* contract"). It is also the **first agent that is not a
RAG agent**, which is why it does not extend ``RagPipelineAgent`` the way
``PolicyAgent`` and ``WorkspaceAgent`` do: with nothing embedded there is no
``RagPipeline`` to adapt, so the "thin adapter over a pipeline" shape simply
doesn't apply (plan decision D8, revision 1).

**How grounding is guaranteed without a confidence gate.** The RAG agents lean on
a cosine threshold to decide whether evidence is good enough to answer from.
There is no similarity score here, so the guarantee has to be structural instead,
and it is enforced in exactly two places:

1. An answer is *only ever* composed from tool output. If the model requests no
   tool, or the tool fails, or the arguments are unparseable, the fixed fallback
   is returned — the agent never asks the model to answer from its own knowledge.
   A plausible-sounding invention about a customer's codebase is worse than "I
   don't know", because the user has no way to tell the difference.
2. ``build_github_answer_prompt`` forbids supplementing the evidence from world
   knowledge of similarly-named open-source projects.

**One tool round, not a loop.** Same decision as the Phase 5 web-search
fallback: one tool-selection call, one bounded read, one answer call. A
multi-step agent loop would multiply latency (already on the critical path with
no cache behind it — plan risk T8) and make the cost of a single question
unbounded.

**Tool output is untrusted.** A README or commit message can be written by any
repository contributor, which is a materially wider authorship surface than a
curated HR policy document. Evidence is fenced and scrubbed identically to
retrieved chunks (Phase 16) before it reaches a prompt.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator

from ..config.settings import GitHubAgentSettings
from ..core.answer_sources import (
    RECOVERY_REASON_INSUFFICIENT_EVIDENCE,
    SOURCE_GITHUB,
    SOURCE_NONE,
)
from ..core.exceptions import ConfigurationError, LLMProviderError, ProviderError, SourceError
from ..llm.routed import default_model_only
from ..core.streaming import chunk_answer
from ..githublive.base import CommitDetail, CommitSummary, GitHubReader, RepoReadme
from ..llm.base import LLMProvider
from ..rag.prompts import (
    GITHUB_TOOLS,
    build_github_answer_prompt,
    build_github_decision_prompt,
    format_repo_catalog,
)
from .base import Agent, AgentResponse, Citation

ReaderBuilder = Callable[..., GitHubReader]

# Parses the "MODE: A|B|C\n\n<answer>" tag ``build_github_answer_prompt`` asks
# for. Deliberately the same shape as ``RagPipeline``'s ``_MODE_TAG_RE`` rather
# than a second convention.
_MODE_TAG_RE = re.compile(r"^\s*MODE:\s*([ABC])\s*\n+(.*)", re.IGNORECASE | re.DOTALL)


def _citations_are_about_only(citations: list[Citation]) -> bool:
    """True when every citation is catalog metadata (``repo#about``), no README/commits."""
    if not citations:
        return False
    return all((c.reference or "").lower().endswith("#about") for c in citations)


def _split_mode_tag(raw: str) -> tuple[str | None, str]:
    """Split a declared mode off the front of a generation, if present.

    An untagged answer returns ``(None, raw)`` and is treated as sufficient — see
    ``GitHubAgent._compose`` for why failing open is the right direction here.
    """
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

        ``reader_builder`` is injected rather than imported so the agent stays
        pure and testable with a fake reader — the same reason ``RagPipeline`` is
        injected into ``PolicyAgent``. It is a *builder* (not a reader) because a
        reader is tenant-scoped: it must be constructed per request from the
        ``org_id`` being served, so one agent instance can never hold onto one
        tenant's credentials.
        """
        self._llm = llm
        self._build_reader = reader_builder
        self._fallback = fallback_response
        self._settings = settings or GitHubAgentSettings.from_env()

    def answer(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
    ) -> AgentResponse:
        # A tenant with no GitHub connection costs zero LLM calls: there is
        # nothing a model could usefully decide without repos to read.
        try:
            reader = self._build_reader(org_id, workspace_id)
        except (ConfigurationError, ProviderError):
            return self._fallback_response()

        try:
            repos = reader.list_repos()
        except ProviderError:
            return self._fallback_response()

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

        # Mode C = nothing relevant. Mode B = partial but useful — yet for a
        # stock-template README the model often picks B with stiff "evidence
        # establishes…" copy instead of C, which used to skip recovery. For
        # get_readme questions, try one supplementary commit fetch on B or C;
        # if that fails, keep a Mode B answer (never invent), or fall back on C.
        #
        # Also recover on Mode A when the *only* grounding is catalog `#about`
        # metadata (README 404). A one-line GitHub description is true but thin;
        # recent commit subjects usually let us write a fuller, still-grounded
        # overview without inventing purpose.
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
        if not answer:
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

        The motivating case, seen live: a "what does this repo do" question fetched
        a README that turned out to be an unmodified project template. The model
        often labels that Mode B (partial) rather than Mode C, so recovery must
        run for both. Recent commit *subjects* describe such a project well.

        Bounded exactly like ``RECOVERY_ENABLED`` on the RAG side: at most one
        extra round, and if it yields no genuinely new evidence we do **not** burn
        a second generation re-reading the same text. Returns ``None`` when
        recovery couldn't help, leaving the caller to fall back.
        """
        name, arguments = decision
        repo = arguments.get("repo")
        if not isinstance(repo, str) or not repo:
            return None
        # Only the repo-overview path benefits: a commit lookup that found nothing
        # relevant isn't improved by listing more commits.
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
            # Honest second look, still nothing. Don't dress it up.
            return None

        return AgentResponse(
            answer=answer,
            grounded=True,
            source=SOURCE_GITHUB,
            citations=list(citations) + list(commit_citations),
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
        chunk_chars: int = 40,
    ) -> tuple[Iterator[str], AgentResponse]:
        """Like ``answer``, but the text arrives as a chunk iterator.

        Chunks an already-decided answer, for the same reason
        ``RagPipeline.answer_stream`` does (Phase 13a): the tool decision can
        still resolve to the fixed fallback *after* the first LLM call, so
        streaming raw tokens could leak text that gets discarded. Not part of the
        abstract ``Agent`` contract — it exists so ``app/api/chat.py`` can treat
        every agent identically at the transport layer.
        """
        result = self.answer(
            question, org_id, conversation_id=conversation_id, workspace_id=workspace_id
        )

        return chunk_answer(result.answer, chunk_chars), result

    # -- internals ---------------------------------------------------------

    def _fallback_response(self) -> AgentResponse:
        """The single place a GitHub refusal is constructed.

        ``SOURCE_NONE`` matches how the RAG agents label a refusal, so callers
        keep branching on the same values they already handle.
        """
        return AgentResponse(
            answer=self._fallback, grounded=False, source=SOURCE_NONE, citations=[]
        )

    def _decide_tool(self, question: str, repos) -> tuple[str, dict] | None:
        """One call: which tool, with which arguments? ``None`` means "no tool"."""
        prompt = build_github_decision_prompt(question, format_repo_catalog(repos))
        try:
            # Pinned to the configured model. Two reasons: choosing a tool is
            # machinery, not an answer; and this agent grounds STRUCTURALLY —
            # no tool call returns the fixed fallback — so a member picking a
            # model with unreliable function calling would silently turn every
            # GitHub question into the fallback, which reads as a broken
            # product rather than a bad model choice. The composed answer
            # (_compose) still follows their pick, because that is the chat.
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

        ``SourceError`` covers both a genuine GitHub failure and a **refused
        repo** (``resolve_repo`` raises it). Refused repos still degrade to the
        fixed fallback so we never confirm whether a private name exists.

        One deliberate exception: ``get_readme`` often 404s when a repo simply
        has no README (common on personal/learning repos). If that repo is in
        the installation's own catalog and carries a description/topics, those
        are enough to ground a short "what is this repo" answer — they were
        stored for exactly that purpose when nothing is embedded.
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
                page = reader.list_pull_requests(
                    repo,
                    limit=int(limit) if isinstance(limit, (int, float)) else 20,
                )
                return self._format_pull_requests(
                    page, state=arguments.get("state")
                )
            if name == "list_reviews":
                number = arguments.get("pull_number")
                if not isinstance(number, (int, float)):
                    return None
                return self._format_reviews(
                    repo, int(number), reader.list_reviews(repo, int(number))
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

        # An unrecognized tool name (a hallucinated function) is not an error to
        # surface — just no evidence.
        return None

    @staticmethod
    def _format_repo_metadata(
        repo: str, known_repos: list
    ) -> tuple[str, list[Citation]] | None:
        """Ground a README miss on the installation catalog, if the repo is listed.

        Matching is against the catalog only — never against GitHub world
        knowledge — so a coaxed foreign name that isn't in ``known_repos`` still
        yields ``None`` and the fixed fallback.
        """
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
        lines.append(
            "Guidance for the answer: expand these fields into a clear overview "
            "of what the repository is for. Do not invent features beyond the "
            "description and topics."
        )
        block = "\n".join(lines)
        about_body = description or ("Topics: " + ", ".join(topics))
        return block, [
            Citation(
                content=about_body[:500],
                reference=f"{matched.full_name}#about",
            )
        ]

    @classmethod
    def _format_readme(
        cls, readme: RepoReadme, *, known_repos: list | None = None
    ) -> tuple[str, list[Citation]]:
        """README plus installation catalog description/topics when available.

        A stock template README alone is often useless for "what does this do?",
        while the org's stored description usually is not — include both.
        """
        header = f"README of {readme.repo}"
        if readme.truncated:
            header += " (truncated — only the beginning is shown)"
        parts = [f"{header}:\n\n{readme.content}"]
        citations = [
            Citation(content=readme.content[:500], reference=f"{readme.repo}#readme")
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
            )
        ]

    @staticmethod
    def _format_pull_requests(
        page, *, state: str | None = None
    ) -> tuple[str, list[Citation]] | None:
        """Render pull requests, keeping the three people distinct.

        Who raised it, who merged it and who reviewed it are different claims,
        so they are separate fields here for the same reason they are separate
        `activity_facts` kinds -- the model must not be able to collapse them
        into "ada did 12 things".
        """
        items = list(getattr(page, "items", ()) or ())
        wanted = (state or "all").lower()
        if wanted == "open":
            items = [p for p in items if p.state == "open"]
        elif wanted == "merged":
            items = [p for p in items if p.state == "merged"]
        if not items:
            # Genuinely no evidence. Falling back beats asking the model to
            # narrate an empty list.
            return None

        repo = items[0].repo
        lines = [f"Pull requests in {repo}:"]
        for pull in items:
            raised = pull.created_at.date().isoformat() if pull.created_at else "unknown date"
            bits = [f"raised {raised} by {pull.author or 'unknown'}"]
            if pull.state == "merged":
                merged = pull.merged_at.date().isoformat() if pull.merged_at else "unknown date"
                # `merged_by` is genuinely absent for some merges (a deleted
                # account, an automation). Said as unknown, never guessed.
                bits.append(f"merged {merged} by {pull.merged_by or 'unknown'}")
            else:
                bits.append(pull.state)
            lines.append(f"- #{pull.number} {pull.title} ({'; '.join(bits)})")

        if getattr(page, "truncated", False):
            # A list built from the newest N while looking complete is the
            # failure that matters, so the model is told rather than trusted to
            # hedge.
            lines.append(
                "(Only the most recent pull requests were read; there may be more.)"
            )

        citations = [
            Citation(content=p.title[:200], reference=f"{p.repo}#{p.number}")
            for p in items[:5]
        ]
        return "\n".join(lines), citations

    @staticmethod
    def _format_reviews(
        repo: str, pull_number: int, reviews: list
    ) -> tuple[str, list[Citation]] | None:
        """Who reviewed one pull request, deduplicated per person.

        Someone who comments four times reviewed one pull request; listing
        every event would make a chatty reviewer look like four.
        """
        if not reviews:
            # An unreviewed pull request is a real, useful answer -- unlike an
            # empty commit list, "nobody has reviewed it" is the information
            # someone asking was after, so this does NOT fall back.
            return (
                f"Pull request #{pull_number} in {repo} has no reviews yet.",
                [Citation(content="no reviews", reference=f"{repo}#{pull_number}")],
            )

        seen: dict[str, str] = {}
        for review in reviews:
            who = getattr(review, "reviewer", None)
            if who and who not in seen:
                seen[who] = getattr(review, "state", "") or "reviewed"

        lines = [f"Reviews on #{pull_number} in {repo}:"]
        for who, verdict in seen.items():
            lines.append(f"- {who}: {verdict.replace('_', ' ').lower()}")
        return "\n".join(lines), [
            Citation(content=", ".join(seen), reference=f"{repo}#{pull_number}")
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
            )
        ]

    @staticmethod
    def _format_commits(
        commits: list[CommitSummary],
    ) -> tuple[str, list[Citation]] | None:
        if not commits:
            # No commits is genuinely no evidence, so fall back rather than ask
            # the model to narrate an empty list.
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
            Citation(content=c.message[:200], reference=f"{c.repo}@{c.sha}")
            for c in commits[:5]
        ]
        return "\n".join(lines), citations
