"""Answer quality for thin GitHub evidence (post-Phase-8 fix).

**The bug this fixes, observed live.** Asked "what does the
persistent-memory-assistant repository do?", the agent fetched the repo's README,
found it was the *stock Vite template* (no project-specific content at all), and
replied: "The provided evidence does not describe what the repository does." That
is honest but useless — and it exposed two structural gaps, not a wording problem:

1. **One tool round only.** When the first evidence turns out uninformative there
   was no second attempt, even though recent commit subjects would have described
   the project perfectly well.
2. **The repo's own description/topics were only consulted when the README
   404'd** — not when a README existed but said nothing about the project.

The fix mirrors machinery this codebase already has rather than inventing any:
graded response modes (like the RAG grounded prompt's A/B/C) plus **at most one**
supplementary evidence round (like ``RECOVERY_ENABLED`` in ``RagPipeline``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.agent.github_agent import GitHubAgent
from app.config.settings import GitHubAgentSettings
from app.githublive import RepoRef
from app.githublive.base import CommitSummary, RepoReadme
from app.llm.base import ChatResult, ToolCall

FALLBACK = "I couldn't find that in the connected GitHub repositories."

# The actual stock Vite template README that triggered the bad answer.
STOCK_VITE_README = (
    "# React + TypeScript + Vite\n\n"
    "This template provides a minimal setup to get React working in Vite with "
    "HMR and some ESLint rules.\n\n"
    "Currently, two official plugins are available:\n"
    "- @vitejs/plugin-react uses Babel for Fast Refresh\n"
)


class _ScriptedLLM:
    """Returns tool calls, then a scripted sequence of generations."""

    def __init__(self, tool_calls, answers):
        self._tool_calls = list(tool_calls)
        self._answers = list(answers)
        self.tool_prompts: list[str] = []
        self.answer_prompts: list[str] = []

    def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
        self.tool_prompts.append(messages[-1]["content"])
        return ChatResult(text=None, tool_calls=list(self._tool_calls))

    def generate(self, prompt, **kwargs):
        self.answer_prompts.append(prompt)
        return self._answers[min(len(self.answer_prompts) - 1, len(self._answers) - 1)]


class _FakeReader:
    def __init__(self, *, readme=None, commits=None):
        self._readme = readme
        self._commits = commits if commits is not None else []
        self.calls: list[str] = []

    def list_repos(self):
        return [
            RepoRef(
                "18sana/persistent-memory-assistant",
                "Long-term memory layer for LLM assistants",
                ("llm", "memory"),
            )
        ]

    def get_readme(self, repo):
        self.calls.append("get_readme")
        return self._readme or RepoReadme(
            repo="18sana/persistent-memory-assistant",
            content=STOCK_VITE_README,
            url="https://github.com/18sana/persistent-memory-assistant#readme",
        )

    def get_commit(self, repo, sha):  # pragma: no cover - unused here
        self.calls.append("get_commit")
        raise AssertionError("not expected in these tests")

    def list_commits(self, repo, *, path=None, since=None, limit=10):
        self.calls.append("list_commits")
        return list(self._commits)


def _commits():
    return [
        CommitSummary(
            repo="18sana/persistent-memory-assistant",
            sha=f"sha{i}",
            message=msg,
            author="Sana",
            date=datetime(2026, 7, i + 1, tzinfo=timezone.utc),
            url=f"https://github.com/18sana/persistent-memory-assistant/commit/sha{i}",
        )
        for i, msg in enumerate(
            [
                "Add vector store for conversation memory",
                "Persist assistant recall across sessions",
                "Wire embeddings into the memory retriever",
            ]
        )
    ]


def _agent(llm, reader, *, settings: GitHubAgentSettings | None = None):
    return GitHubAgent(
        llm=llm,
        reader_builder=lambda org_id, workspace_id=None: reader,
        fallback_response=FALLBACK,
        settings=settings or GitHubAgentSettings(),
    )


def _readme_call():
    return [
        ToolCall(
            id="c1",
            name="get_readme",
            arguments=json.dumps({"repo": "persistent-memory-assistant"}),
        )
    ]


# -- gap 2: catalog metadata must accompany a README, not only replace it ---


def test_repo_description_is_included_alongside_the_readme():
    """A stub README plus a real description is still groundable evidence.

    Previously the description was consulted only when the README 404'd, so a
    stock-template README produced an answer with no project context at all --
    even though the installation catalog knew what the repo was.
    """
    llm = _ScriptedLLM(_readme_call(), ["MODE: A\n\nA long-term memory layer."])
    agent = _agent(llm, _FakeReader())

    agent.answer("What does persistent-memory-assistant do?", "org-1")

    evidence = llm.answer_prompts[0]
    assert "Long-term memory layer for LLM assistants" in evidence
    assert "llm" in evidence  # topics carried too
    assert "React + TypeScript + Vite" in evidence  # README still present


# -- gap 1: one bounded supplementary round -------------------------------


def test_insufficient_evidence_triggers_one_supplementary_fetch():
    """MODE: C on the first pass must gather more, not give up.

    This is the exact live failure: README fetched, README useless, answer
    useless. Recent commit subjects describe the project, so fetch them.
    """
    llm = _ScriptedLLM(
        _readme_call(),
        [
            "MODE: C\n\nThe evidence does not describe what this repository does.",
            "MODE: A\n\nIt is a persistent memory layer for LLM assistants.",
        ],
    )
    reader = _FakeReader(commits=_commits())
    agent = _agent(llm, reader)

    response = agent.answer("What does persistent-memory-assistant do?", "org-1")

    assert reader.calls == ["get_readme", "list_commits"]
    assert len(llm.answer_prompts) == 2
    # The second prompt must carry the commit evidence the first one lacked.
    assert "Add vector store for conversation memory" in llm.answer_prompts[1]
    # And the user sees the better answer, with the tag stripped.
    assert response.answer == "It is a persistent memory layer for LLM assistants."
    assert response.grounded is True
    assert response.recovery_used is True
    assert response.response_mode == "A"


def test_supplementary_round_happens_at_most_once():
    """Bounded like RECOVERY_ENABLED -- never a loop chasing better evidence."""
    llm = _ScriptedLLM(
        _readme_call(),
        ["MODE: C\n\nNot described.", "MODE: C\n\nStill not described."],
    )
    reader = _FakeReader(commits=_commits())
    agent = _agent(llm, reader)

    response = agent.answer("What does it do?", "org-1")

    assert reader.calls.count("list_commits") == 1
    assert len(llm.answer_prompts) == 2, "exactly one retry, never a loop"
    # Genuinely unanswerable after one honest retry -> the fixed fallback.
    assert response.grounded is False
    assert response.answer == FALLBACK


def test_sufficient_first_answer_costs_no_extra_fetch():
    llm = _ScriptedLLM(_readme_call(), ["MODE: A\n\nIt reconciles invoices."])
    reader = _FakeReader(commits=_commits())
    agent = _agent(llm, reader)

    response = agent.answer("What does it do?", "org-1")

    assert reader.calls == ["get_readme"]
    assert len(llm.answer_prompts) == 1
    assert response.recovery_used is False
    assert response.answer == "It reconciles invoices."


def test_partial_evidence_mode_b_triggers_recovery_for_thin_readme():
    """Mode B on a stock-template README should fetch commits once, not stall.

    Live models often pick B (partial) instead of C for thin READMEs — recovery
    must run for both so the user gets a grounded, useful answer.
    """
    llm = _ScriptedLLM(
        _readme_call(),
        [
            "MODE: B\n\nHere's what I can see: the README is still the stock Vite "
            "template, so it doesn't say what this project is for.",
            "MODE: A\n\nIt's a persistent memory layer for LLM assistants — recent "
            "commits add a vector store and persist recall across sessions.",
        ],
    )
    reader = _FakeReader(commits=_commits())
    agent = _agent(llm, reader)

    response = agent.answer("What does it do?", "org-1")

    assert reader.calls == ["get_readme", "list_commits"]
    assert response.grounded is True
    assert response.recovery_used is True
    assert response.response_mode == "A"
    assert "persistent memory" in response.answer.lower()
    assert not response.answer.startswith("MODE:")


def test_mode_b_is_kept_when_recovery_finds_nothing_more():
    """If commits are empty, keep the friendly partial Mode B — never invent."""
    partial = (
        "MODE: B\n\nHere's what I can see: the README is the stock Vite template, "
        "so it describes tooling rather than this project's purpose. Try asking "
        "about recent commits."
    )
    llm = _ScriptedLLM(_readme_call(), [partial])
    agent = _agent(llm, _FakeReader(commits=[]))

    response = agent.answer("What does it do?", "org-1")

    assert response.grounded is True
    assert response.response_mode == "B"
    assert "stock Vite template" in response.answer
    assert response.recovery_used is False


def test_recovery_can_be_disabled_by_configuration():
    llm = _ScriptedLLM(_readme_call(), ["MODE: C\n\nNot described."])
    reader = _FakeReader(commits=_commits())
    agent = _agent(
        llm, reader, settings=GitHubAgentSettings(evidence_recovery_enabled=False)
    )

    response = agent.answer("What does it do?", "org-1")

    assert reader.calls == ["get_readme"]
    assert len(llm.answer_prompts) == 1
    assert response.grounded is False


def test_recovery_is_skipped_when_there_is_nothing_more_to_fetch():
    """No commits either -> don't burn a second generation for the same evidence."""
    llm = _ScriptedLLM(_readme_call(), ["MODE: C\n\nNot described."])
    reader = _FakeReader(commits=[])
    agent = _agent(llm, reader)

    response = agent.answer("What does it do?", "org-1")

    assert reader.calls == ["get_readme", "list_commits"]
    assert len(llm.answer_prompts) == 1, "no new evidence means no second generation"
    assert response.grounded is False


# -- back-compat -----------------------------------------------------------


def test_an_untagged_answer_is_treated_as_sufficient():
    """A model that forgets the tag must not trigger an endless recovery.

    Failing open here is the safe direction: the cost is one missed retry, while
    failing closed would spend a second LLM call on every untagged answer.
    """
    llm = _ScriptedLLM(_readme_call(), ["It reconciles invoices nightly."])
    reader = _FakeReader(commits=_commits())
    agent = _agent(llm, reader)

    response = agent.answer("What does it do?", "org-1")

    assert reader.calls == ["get_readme"]
    assert response.answer == "It reconciles invoices nightly."
    assert response.response_mode is None
    assert response.grounded is True


def test_mode_tag_never_leaks_into_the_user_visible_answer():
    llm = _ScriptedLLM(_readme_call(), ["MODE: A\n\nClean answer."])
    agent = _agent(llm, _FakeReader())

    response = agent.answer("What does it do?", "org-1")

    assert "MODE" not in response.answer
    assert response.answer == "Clean answer."

def test_about_only_mode_a_fetches_commits_for_richer_answer():
    """README 404 + Mode A from catalog description still recovers commits.

    Thin `#about` evidence alone produces short paraphrases; one commit round
    gives the model something concrete to expand into a fuller overview.
    """
    from app.core.exceptions import SourceError

    llm = _ScriptedLLM(
        _readme_call(),
        [
            "MODE: A\n\nIt is a long-term memory layer.",
            "MODE: A\n\n"
            "persistent-memory-assistant is a long-term memory layer for LLM "
            "assistants. Recent work added a vector store for conversation "
            "memory and retrieval APIs — so beyond the GitHub description, "
            "the commit history shows active memory/retrieval work.",
        ],
    )

    class _NoReadme(_FakeReader):
        def get_readme(self, repo):
            self.calls.append("get_readme")
            raise SourceError("README not found")

    reader = _NoReadme(commits=_commits())
    agent = _agent(llm, reader)
    response = agent.answer("What does persistent-memory-assistant do?", "org-1")

    assert reader.calls == ["get_readme", "list_commits"]
    assert len(llm.answer_prompts) == 2
    assert "Add vector store for conversation memory" in llm.answer_prompts[1]
    assert response.grounded is True
    assert response.recovery_used is True
    assert "vector store" in response.answer.lower()

