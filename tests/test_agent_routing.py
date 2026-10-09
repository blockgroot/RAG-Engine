"""Auto-routing: the question picks the agent, not a tab.

Real Postgres with real embeddings would be the honest end-to-end test, but the
embedder is a 500MB model and this suite must stay runnable — so the vector
probe is stubbed and what is asserted is the DECISION LOGIC plus, separately,
that the probe's SQL is correctly scoped (which is the part a fake cannot
verify, so it gets its own real-DB tests).

The load-bearing property throughout: a misroute costs a REFUSAL, never a
wrong answer. Every routed agent still runs its own confidence gate and strict
prompt, so these tests are about reachability and scope, not correctness of
answers.
"""

from __future__ import annotations

import uuid

import pytest

from app.agent import routing
from app.agent.orchestration import INSIGHTS_KEY, POLICY_KEY, WORKSPACE_KEY
from app.insights.resolve import ChartSpec
from app.auth import OAuthTokens, save_connection
from app.auth.credentials import set_connection_config
from app.auth.users import invite_member
from app.workspaces.store import create_workspace

from .conftest import requires_db

ORG = "11111111-1111-1111-1111-111111111111"

#: Captured at import, because the autouse fixture below stubs the question
#: check off for every cosine test. The github_live tests need the real one.
_real_question_route = routing._try_question_route


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    # Cosine-routing tests must not spend a live classifier call.
    monkeypatch.setattr(routing, "_try_question_route", lambda *a, **k: None)


def _stub(monkeypatch, *, connected: set[str], scores: dict | None = None, repos=None):
    """Replace the three things that touch the world."""
    monkeypatch.setattr(routing, "_connected_providers", lambda *a, **k: connected)
    monkeypatch.setattr(routing, "_probe_scores", lambda *a, **k: scores or {})
    monkeypatch.setattr(
        routing,
        "_named_repo",
        lambda question, *a, **k: next(
            (r for r in (repos or []) if r.split("/")[-1].lower() in question.lower()),
            None,
        ),
    )


# --------------------------------------------------------------------------
# The basic promise: the best-matching source answers
# --------------------------------------------------------------------------


def test_the_best_scoring_provider_wins(monkeypatch):
    _stub(
        monkeypatch,
        connected={"notion", "slack", "linear"},
        scores={"notion": 0.71, "slack": 0.42, "linear": 0.38},
    )

    decision = routing.choose_agent("what is the refund policy?", ORG)

    assert decision.agent_key == "notion"
    assert decision.reason == "best-match"
    assert decision.scores["notion"] == 0.71


def test_scores_are_returned_for_diagnosis(monkeypatch):
    """Routing quality is not observable without them, and CLAUDE.md's standing
    advice is to validate thresholds against real logged scores."""
    _stub(monkeypatch, connected={"notion", "slack"}, scores={"notion": 0.6, "slack": 0.5})

    assert routing.choose_agent("q", ORG).scores == {"notion": 0.6, "slack": 0.5}


def test_a_single_embedded_source_skips_the_probe(monkeypatch):
    """No decision to make, so no embedding and no query — the common
    single-source tenant should pay nothing for routing."""
    probed = []
    monkeypatch.setattr(routing, "_connected_providers", lambda *a, **k: {"notion"})
    monkeypatch.setattr(
        routing, "_probe_scores", lambda *a, **k: probed.append(1) or {}
    )

    decision = routing.choose_agent("anything", ORG)

    assert decision.agent_key == "notion"
    assert decision.reason == "only-source"
    assert probed == [], "must not embed when there is nothing to choose between"


# --------------------------------------------------------------------------
# GitHub, which has no embeddings and would otherwise be unreachable
# --------------------------------------------------------------------------


def test_naming_a_repo_beats_a_high_scoring_document(monkeypatch):
    """A Notion page ABOUT a repo would otherwise outscore the repo itself.
    Nothing else in the org is called that, so it is the least ambiguous
    signal available and must win."""
    _stub(
        monkeypatch,
        connected={"github", "notion"},
        scores={"notion": 0.88},
        repos=["18-sana/Chain-Guard"],
    )

    decision = routing.choose_agent("what changed in Chain-Guard?", ORG)

    assert decision.agent_key == "github"
    assert decision.reason == "repo-named"


def test_code_intent_only_applies_when_nothing_embedded_can_answer(monkeypatch):
    """Deliberately last: a code word inside a document question must not
    hijack it."""
    _stub(monkeypatch, connected={"github", "notion"}, scores={"notion": 0.72})

    decision = routing.choose_agent("what is our branch naming convention?", ORG)

    assert decision.agent_key == "notion", "a strong document match wins"

    _stub(monkeypatch, connected={"github", "notion"}, scores={"notion": 0.11})

    decision = routing.choose_agent("which commits landed this week?", ORG)

    assert decision.agent_key == "github"
    assert decision.reason == "code-intent"


def test_github_alone_is_reachable_without_any_signal(monkeypatch):
    """A tenant whose only source is GitHub must not be routed to the policy
    agent, which has nothing."""
    _stub(monkeypatch, connected={"github"}, scores={})

    decision = routing.choose_agent("anything at all", ORG)

    assert decision.agent_key == "github"


def test_a_code_word_never_reaches_github_when_it_is_not_connected(monkeypatch):
    _stub(monkeypatch, connected={"notion"}, scores={"notion": 0.1})

    assert routing.choose_agent("which commits landed?", ORG).agent_key == "notion"


# --------------------------------------------------------------------------
# Weak matches: refuse honestly rather than route somewhere blind
# --------------------------------------------------------------------------


def test_a_weak_best_match_still_routes_to_the_closest_source(monkeypatch):
    """Routing to the default agent instead would refuse with NO citations from
    the source that was closest. The routed agent's own gate produces the same
    refusal, but from the right place."""
    _stub(monkeypatch, connected={"notion", "slack"}, scores={"notion": 0.2, "slack": 0.1})

    decision = routing.choose_agent("who won the world cup?", ORG)

    assert decision.agent_key == "notion"
    assert decision.reason == "weak-best-match"


def test_no_connections_falls_back_to_the_previous_default(monkeypatch):
    """Unchanged behaviour for a tenant that has connected nothing."""
    _stub(monkeypatch, connected=set(), scores={})

    assert routing.choose_agent("q", ORG).agent_key == POLICY_KEY
    assert (
        routing.choose_agent("q", ORG, workspace_id="ws-1").agent_key == WORKSPACE_KEY
    )


def test_an_explicit_request_still_wins(monkeypatch):
    """The API keeps accepting `agent`, so existing callers and every test that
    pins a source keep working."""
    _stub(monkeypatch, connected={"notion"}, scores={"notion": 0.9})

    decision = routing.choose_agent("q", ORG, requested_agent="slack")

    assert decision.agent_key == "slack"
    assert decision.reason == "requested"


def test_an_unknown_requested_agent_is_ignored_not_trusted(monkeypatch):
    """`workspace` and `policy` are internal keys — a client must not be able
    to select one through the public `agent` field."""
    _stub(monkeypatch, connected={"notion"}, scores={"notion": 0.9})

    assert routing.choose_agent("q", ORG, requested_agent="workspace").agent_key == "notion"


# --------------------------------------------------------------------------
# Failing open, never failing the question
# --------------------------------------------------------------------------


def test_a_broken_connection_lookup_does_not_fail_the_question(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(routing, "_connected_providers", _boom)

    assert routing.choose_agent("q", ORG).agent_key == POLICY_KEY


def test_a_broken_embedder_does_not_fail_the_question(monkeypatch):
    """The probe is best-effort. A dead embedder must degrade routing to the
    default agent, not 500 a chat request — and it must not raise out of
    _probe_scores, which is the function that actually touches it."""
    monkeypatch.setattr(routing, "_connected_providers", lambda *a, **k: {"notion", "slack"})

    def _boom(*a, **k):
        raise RuntimeError("embedder is down")

    monkeypatch.setattr("app.embeddings.build_embedding_provider", _boom)

    # The probe swallows it and returns no scores...
    assert routing._probe_scores("q", ORG, None, {"notion", "slack"}) == {}
    # ...and with no scores and no GitHub, routing lands on the default.
    assert routing.choose_agent("q", ORG).agent_key == POLICY_KEY


# --------------------------------------------------------------------------
# The probe's SQL — the part a stub cannot verify
# --------------------------------------------------------------------------


@requires_db
def test_the_probe_never_sees_another_tenants_content(store, org_cleanup, monkeypatch):
    """A routing decision informed by content the asker cannot read would leak
    which sources another org uses, and route on their data."""
    mine = store.create_organization(f"Route Mine {uuid.uuid4().hex[:8]}")
    theirs = store.create_organization(f"Route Theirs {uuid.uuid4().hex[:8]}")
    org_cleanup.extend([mine, theirs])

    vec = [0.0] * 1024
    store.upsert_source_document(
        theirs,
        provider="notion",
        external_id="t-1",
        title="Their page",
        chunks=["secret"],
        embeddings=[vec],
    )

    monkeypatch.setattr(
        "app.embeddings.build_embedding_provider",
        lambda *a, **k: type("E", (), {"embed": lambda self, texts: [vec]})(),
    )

    assert routing._probe_scores("q", mine, None, {"notion"}) == {}


@requires_db
def test_the_probe_is_scoped_to_the_space(store, org_cleanup, monkeypatch):
    """A space-scoped question must not be routed on org-wide content — a space
    sees ONLY its own rows (CLAUDE.md §3)."""
    org_id = store.create_organization(f"Route Space {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    owner = invite_member(f"o-{uuid.uuid4().hex[:8]}@example.com", org_id)
    space = create_workspace(org_id, "Meeting notes", owner.id)

    vec = [0.0] * 1024
    store.upsert_source_document(
        org_id,
        provider="notion",
        external_id="org-wide-1",
        title="Company handbook",
        chunks=["org wide content"],
        embeddings=[vec],
    )
    monkeypatch.setattr(
        "app.embeddings.build_embedding_provider",
        lambda *a, **k: type("E", (), {"embed": lambda self, texts: [vec]})(),
    )

    assert routing._probe_scores("q", org_id, space, {"notion"}) == {}
    assert routing._probe_scores("q", org_id, None, {"notion"}) != {}


@requires_db
def test_connected_providers_is_scoped_and_skips_dead_tokens(store, org_cleanup):
    """A connection needing reauth must not be routed to: the agent would fail
    on a dead token, which reads as the product being broken."""
    org_id = store.create_organization(f"Route Conn {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    for provider in ("notion", "slack"):
        save_connection(
            org_id,
            provider,
            OAuthTokens(
                access_token=f"tok-{provider}",
                refresh_token=None,
                expires_at=None,
                external_workspace_id=f"ext-{uuid.uuid4().hex[:6]}",
            ),
        )

    assert routing._connected_providers(org_id, None) == {"notion", "slack"}

    from app.db.connection import get_connection

    with get_connection() as conn:
        conn.execute(
            "UPDATE oauth_connections SET needs_reauth = true "
            "WHERE org_id = %s AND provider = 'slack'",
            (org_id,),
        )

    assert routing._connected_providers(org_id, None) == {"notion"}


@requires_db
def test_a_named_repo_is_matched_against_authorized_repos_only(store, org_cleanup):
    """Otherwise a question mentioning any public repository could steer
    routing."""
    org_id = store.create_organization(f"Route Repo {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    save_connection(
        org_id,
        "github",
        OAuthTokens(
            access_token="tok",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="gh-1",
        ),
    )
    set_connection_config(
        org_id,
        "github",
        {"installation_id": "1", "repos": [{"full_name": "acme/chain-guard"}]},
    )

    assert routing._named_repo("what changed in chain-guard?", org_id, None) == (
        "acme/chain-guard"
    )
    assert routing._named_repo("what about tensorflow/tensorflow?", org_id, None) is None
    # Substring inside a longer word is not a hit.
    assert routing._named_repo("chain-guarded routes", org_id, None) is None


# --------------------------------------------------------------------------
# The probe sits on the critical path, so its timeout must be bounded
# --------------------------------------------------------------------------


def test_the_probe_caps_a_remote_embedders_timeout(monkeypatch):
    """EMBEDDING_TIMEOUT defaults to 60s. Unbounded, a hanging remote embedder
    costs 60s BEFORE the pipeline starts (which then has its own 60s) — a
    two-minute wait to be handed a refusal, since a timed-out probe degrades to
    the default agent."""
    routing.reset_probe_embedder_for_tests()
    monkeypatch.setenv("EMBEDDING_BACKEND", "remote")
    monkeypatch.setenv("EMBEDDING_TIMEOUT", "60")
    monkeypatch.setenv("EMBEDDING_API_KEY", "k")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embed.example.com/v1")

    built = {}
    monkeypatch.setattr(
        "app.embeddings.build_embedding_provider",
        lambda settings=None: built.setdefault("settings", settings),
    )

    routing._probe_embedder()

    assert built["settings"].timeout == routing.DEFAULT_PROBE_TIMEOUT_SECONDS
    routing.reset_probe_embedder_for_tests()


def test_the_probe_never_raises_a_configured_timeout(monkeypatch):
    """A deployment that deliberately set a SHORTER timeout must keep it."""
    routing.reset_probe_embedder_for_tests()
    monkeypatch.setenv("EMBEDDING_BACKEND", "remote")
    monkeypatch.setenv("EMBEDDING_TIMEOUT", "2")
    monkeypatch.setenv("EMBEDDING_API_KEY", "k")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embed.example.com/v1")

    built = {}
    monkeypatch.setattr(
        "app.embeddings.build_embedding_provider",
        lambda settings=None: built.setdefault("settings", settings),
    )

    routing._probe_embedder()

    assert built["settings"].timeout == 2.0
    routing.reset_probe_embedder_for_tests()


def test_the_probe_embedder_is_built_once(monkeypatch):
    """RemoteEmbeddingProvider is uncached in the factory and builds a fresh
    HTTP client each call — one per question would throw away connection reuse
    on the critical path."""
    routing.reset_probe_embedder_for_tests()
    monkeypatch.setenv("EMBEDDING_BACKEND", "remote")
    monkeypatch.setenv("EMBEDDING_API_KEY", "k")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embed.example.com/v1")

    builds = []
    monkeypatch.setattr(
        "app.embeddings.build_embedding_provider",
        lambda settings=None: builds.append(1) or object(),
    )

    first = routing._probe_embedder()
    second = routing._probe_embedder()

    assert first is second
    assert len(builds) == 1
    routing.reset_probe_embedder_for_tests()


def test_the_probe_uses_the_same_model_as_retrieval(monkeypatch):
    """The probe's vector is compared against STORED chunk embeddings, so a
    different model would make every cosine meaningless, not merely wrong."""
    routing.reset_probe_embedder_for_tests()
    monkeypatch.setenv("EMBEDDING_BACKEND", "remote")
    monkeypatch.setenv("EMBEDDING_MODEL", "bge-m3")
    monkeypatch.setenv("EMBEDDING_API_KEY", "k")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://embed.example.com/v1")

    built = {}
    monkeypatch.setattr(
        "app.embeddings.build_embedding_provider",
        lambda settings=None: built.setdefault("settings", settings),
    )

    routing._probe_embedder()

    from app.config.settings import EmbeddingSettings

    assert built["settings"].model == EmbeddingSettings.from_env().model
    routing.reset_probe_embedder_for_tests()


def test_chart_mode_goes_straight_to_a_chart(monkeypatch):
    """The metric's provider is the connector. Cosine would send this to Linear
    RAG, which would invent a total from issue text."""
    from app.insights.resolve import AskIntent

    _stub(monkeypatch, connected={"linear"})
    spec = ChartSpec(metric="issues_completed", group_by="subject", period="month",
                     chart="pie")
    monkeypatch.setattr("app.insights.resolve.classify_question",
                        lambda q, **k: AskIntent("chart", spec=spec))

    decision = routing.choose_agent("share of completed work by team", ORG, chart_mode=True)

    assert (decision.agent_key, decision.reason) == (INSIGHTS_KEY, "chart")
    assert decision.chart_spec == spec


def test_a_page_in_another_workspace_is_named_instead_of_turn_off_chart(monkeypatch):
    """Company-wide asked for the hiring plan, which lives in a workspace.
    That used to read as "turn off Chart", as if they had wanted a written answer."""
    from types import SimpleNamespace

    from app.insights.resolve import AskIntent

    _stub(monkeypatch, connected={"notion"})
    monkeypatch.setattr("app.insights.resolve.classify_question",
                        lambda q, **k: AskIntent("qa"))
    monkeypatch.setattr(
        "app.doctables.store.document_places",
        lambda **k: [SimpleNamespace(title="Hiring plan 2026", workspace_name="Coding",
                                     has_table=True)],
    )
    decision = routing.choose_agent(
        "From the hiring plan 2026 document, show the total salary budget for each team.",
        ORG, chart_mode=True, viewer=object(),
    )
    assert decision.reason == "chart-refuse"
    assert "Hiring plan 2026" in decision.chart_refusal
    assert "Coding" in decision.chart_refusal
    assert "Turn off Chart" not in decision.chart_refusal


def test_a_named_page_with_no_table_here_is_said_plainly(monkeypatch):
    """The quarterly budget page was not among the tables offered, and the
    reply blamed themes in text instead of saying the page is not here."""
    from app.insights.resolve import AskIntent

    _stub(monkeypatch, connected={"notion"})
    monkeypatch.setattr(
        "app.insights.resolve.classify_question",
        lambda q, **k: AskIntent("refuse", message="no",
                                 named_document="quarterly budget page"),
    )
    monkeypatch.setattr("app.doctables.store.document_places", lambda **k: [])
    decision = routing.choose_agent(
        "From the quarterly budget page in Notion, show the total budget for each department.",
        ORG, chart_mode=True, viewer=object(),
    )
    assert "quarterly budget page" in decision.chart_refusal
    assert "don't see" in decision.chart_refusal
    assert "can't be shown as a chart" not in decision.chart_refusal


def test_chart_mode_never_falls_back_to_a_document_answer(monkeypatch):
    """RAG must not invent a number when a chart was asked for."""
    from app.insights.resolve import AskIntent

    _stub(monkeypatch, connected={"linear", "notion"}, scores={"notion": 0.9})
    monkeypatch.setattr("app.insights.resolve.classify_question",
                        lambda q, **k: AskIntent("qa"))

    decision = routing.choose_agent("what is our leave policy?", ORG, chart_mode=True)

    assert (decision.agent_key, decision.reason) == (INSIGHTS_KEY, "chart-mode-text-question")
    assert decision.chart_refusal.startswith("**Chart mode only builds charts**")


def test_chart_mode_names_what_can_be_charted_when_it_cannot_count(monkeypatch):
    from app.insights.resolve import AskIntent

    _stub(monkeypatch, connected={"linear"})
    monkeypatch.setattr("app.insights.resolve.classify_question",
                        lambda q, **k: AskIntent("chart", spec=None))
    decision = routing.choose_agent("chart of team happiness", ORG, chart_mode=True)
    assert decision.reason == "chart-refuse"
    assert decision.chart_refusal.startswith("**This can't be shown as a chart**")


def test_chart_mode_does_not_offer_a_live_code_read(monkeypatch):
    seen = {}

    def classify(q, **k):
        from app.insights.resolve import AskIntent

        seen.update(k)
        return AskIntent("refuse", message="no")

    _stub(monkeypatch, connected={"github"})
    monkeypatch.setattr("app.insights.resolve.classify_question", classify)
    routing.choose_agent("chart who owns the auth code", ORG, chart_mode=True)
    assert seen["offer_github"] is False and seen["fail_open"] is False


def test_normal_ask_never_runs_the_chart_classifier(monkeypatch):
    """Charts have their own mode; Ask must not pay for the chart prompt."""
    def boom(*a, **k):
        raise AssertionError("the chart classifier ran outside Chart mode")

    _stub(monkeypatch, connected={"linear", "notion"}, scores={"notion": 0.8})
    monkeypatch.setattr("app.insights.resolve.classify_question", boom)
    monkeypatch.setattr(routing, "_try_question_route", _real_question_route)
    monkeypatch.setattr("app.insights.resolve.classify_route",
                        lambda q, **k: __import__("app.insights.resolve", fromlist=["x"]).AskIntent("qa", needs_live=True))

    decision = routing.choose_agent("how many issues did we close?", ORG)
    assert decision.agent_key == "notion"
    assert decision.needs_live is True


# ---------------------------------------------------------------------------
# `github_live`: the classifier reaching GitHubAgent.
#
# GitHub embeds nothing, so it can never win the cosine probe, and
# `_CODE_INTENT` cannot be widened to close the gap -- no regex separates
# "auth code" from "code of conduct", and a code of conduct is a document.
# So the classifier gets one extra outcome, and the keyword rules STAY as the
# floor beneath it because the classifier fails open.
# ---------------------------------------------------------------------------


def test_a_classified_code_question_routes_to_github(monkeypatch):
    from app.insights.resolve import AskIntent

    _stub(monkeypatch, connected={"github", "notion"}, scores={"notion": 0.8})
    # The autouse fixture stubs the whole route off; restore the real one so
    # the new branch is actually exercised.
    monkeypatch.setattr(routing, "_try_question_route", _real_question_route)
    monkeypatch.setattr(
        "app.insights.resolve.classify_route",
        lambda q, **k: AskIntent("github_live"),
    )
    monkeypatch.setattr(routing, "_has_authorized_repos", lambda *a, **k: True)

    decision = routing.choose_agent("who owns the auth code?", ORG)
    assert decision.agent_key == "github"
    assert decision.reason == "classified-code-question"


def test_a_code_question_with_no_authorized_repos_falls_through(monkeypatch):
    """An installation authorizing zero repos has nothing to say, and routing
    there produces a fallback that reads as the product being broken."""
    from app.insights.resolve import AskIntent

    _stub(monkeypatch, connected={"github", "notion"}, scores={"notion": 0.8})
    monkeypatch.setattr(routing, "_try_question_route", _real_question_route)
    monkeypatch.setattr(
        "app.insights.resolve.classify_route",
        lambda q, **k: AskIntent("github_live"),
    )
    monkeypatch.setattr(routing, "_has_authorized_repos", lambda *a, **k: False)

    decision = routing.choose_agent("who owns the auth code?", ORG)
    assert decision.agent_key == "notion", "must fall through, not route to github"


def test_the_repo_check_reads_authorized_repos_not_activity_facts(monkeypatch):
    """GitHubAgent reads LIVE, so a freshly connected installation with no
    recorded facts can still answer. Gating on facts would block a real answer
    for up to a whole sync interval."""
    monkeypatch.setattr(
        "app.auth.credentials.get_connection_config",
        lambda *a, **k: {"repos": [{"full_name": "acme/api"}]},
    )
    assert routing._has_authorized_repos("org-1", None) is True

    monkeypatch.setattr(
        "app.auth.credentials.get_connection_config", lambda *a, **k: {"repos": []}
    )
    assert routing._has_authorized_repos("org-1", None) is False


def test_the_repo_check_never_raises(monkeypatch):
    """Routing must never fail a question."""
    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.auth.credentials.get_connection_config", _boom)
    assert routing._has_authorized_repos("org-1", None) is False


def test_code_intent_still_routes_when_the_classifier_says_qa(monkeypatch):
    """The floor. `classify_route` fails open, so a dead or rate-limited
    classifier silently un-routes GitHub -- the keyword rules must remain."""
    _stub(monkeypatch, connected={"github", "notion"}, scores={"notion": 0.20})

    decision = routing.choose_agent("what changed in the pull requests?", ORG)
    assert decision.agent_key == "github"
    assert decision.reason == "code-intent"


def test_a_document_question_is_not_pulled_to_github(monkeypatch):
    """The regression risk of a third outcome: a plain policy question must
    still reach the document corpus that can answer it."""
    _stub(monkeypatch, connected={"github", "notion"}, scores={"notion": 0.62})

    decision = routing.choose_agent("what is our parental leave policy?", ORG)
    assert decision.agent_key == "notion"
    assert decision.reason == "best-match"


def test_a_weak_follow_up_is_routed_with_the_turn_it_follows(monkeypatch):
    """"Elaborate and describe in detail" after a Drive answer landed on
    Notion: it names no source, so alone it scores under the gate everywhere."""
    _stub(monkeypatch, connected={"notion", "google"})

    def probe(text, *a, **k):
        if "AI Development Ecosystem" in text:
            return {"google": 0.62, "notion": 0.30}
        return {"google": 0.53, "notion": 0.56}   # flat, and above the gate

    monkeypatch.setattr(routing, "_probe_scores", probe)
    monkeypatch.setattr(routing, "_try_question_route", lambda *a, **k: None)

    decision = routing.choose_agent(
        "Elaborate and describe in detail.", ORG,
        context='What are the key rules in "AI Development Ecosystem"?',
    )
    assert (decision.agent_key, decision.reason) == ("google", "follow-up")
    # Without a previous turn nothing changes.
    assert routing.choose_agent("Elaborate and describe in detail.", ORG).agent_key == "notion"


def test_a_new_topic_in_the_same_chat_is_routed_on_its_own_words(monkeypatch):
    _stub(monkeypatch, connected={"notion", "linear"})
    monkeypatch.setattr(routing, "_try_question_route", lambda *a, **k: None)
    monkeypatch.setattr(
        routing, "_probe_scores",
        lambda text, *a, **k: {"linear": 0.75, "notion": 0.63} if "SYV-5" in text
        else {"linear": 0.45, "notion": 0.73},
    )
    decision = routing.choose_agent(
        "What is our leave policy?", ORG, context="What's the status of SYV-5?"
    )
    assert (decision.agent_key, decision.reason) == ("notion", "best-match")


def test_chart_mode_on_a_model_without_charts_names_the_ones_that_can(monkeypatch):
    """A model that cannot build charts must not make the QUESTION look unchartable."""
    from app.llm.routed import use_model

    def boom(*a, **k):
        raise AssertionError("the classifier must not run on a model without charts")

    _stub(monkeypatch, connected={"linear"})
    monkeypatch.setattr("app.insights.resolve.classify_question", boom)
    monkeypatch.setenv("LLM_MODEL", "gemini-2.5-flash")
    use_model("cohere/north-mini-code:free")
    try:
        decision = routing.choose_agent("Linear issues by priority", ORG, chart_mode=True)
    finally:
        use_model(None)
    assert decision.reason == "chart-model-unsupported"
    assert decision.chart_refusal.startswith("**Cohere North Mini doesn't support charts**")
    assert "gemini-2.5-flash or Qwen 3.8 27B" in decision.chart_refusal


@pytest.mark.parametrize("model", [None, "qwen/qwen3.8-27b", "acme-own-model"])
def test_models_that_build_charts_are_not_stopped(model):
    from app.llm.routed import use_model

    use_model(model)
    try:
        assert routing._chart_unsupported_model() is None
    finally:
        use_model(None)



def test_named_tools_decide_where_the_answer_comes_from(monkeypatch):
    """"Latest updates in Linear and Drive" was routed to Slack, whose
    #rag-updates channel scored highest on "updates", and cited only Slack."""
    _stub(monkeypatch, connected={"linear", "google", "slack", "notion"},
          scores={"slack": 0.71, "linear": 0.52, "google": 0.48, "notion": 0.4})
    decision = routing.choose_agent(
        "Fetch me the latest updates in linear and drive, what's the current status?", ORG)
    assert (decision.agent_key, decision.reason) == ("linear", "tools-named")


def test_one_named_tool_is_routed_there(monkeypatch):
    _stub(monkeypatch, connected={"linear", "slack"}, scores={"slack": 0.9, "linear": 0.2})
    decision = routing.choose_agent("what changed in linear this week?", ORG)
    assert (decision.agent_key, decision.reason) == ("linear", "tool-named")


def test_no_named_tool_still_uses_the_probe(monkeypatch):
    _stub(monkeypatch, connected={"linear", "slack"}, scores={"slack": 0.9, "linear": 0.2})
    assert routing.choose_agent("what did we decide about the offsite?", ORG).agent_key == "slack"



def test_a_chart_ask_in_ask_gets_the_chart_mode_hint(monkeypatch):
    from app.insights.resolve import AskIntent

    _stub(monkeypatch, connected={"linear", "notion"}, scores={"notion": 0.8})
    monkeypatch.setattr(routing, "_try_question_route", _real_question_route)
    monkeypatch.setattr("app.insights.resolve.classify_route",
                        lambda q, **k: AskIntent("qa", chart_ask="visual"))
    decision = routing.choose_agent("show Linear issues as a graph", ORG)
    assert (decision.agent_key, decision.reason) == (INSIGHTS_KEY, "chart-mode-off")
    assert decision.chart_refusal.startswith("**Charts are in Chart mode**")


def test_in_slack_a_chart_ask_is_answered_in_chart_mode(monkeypatch):
    from app.insights.resolve import AskIntent

    _stub(monkeypatch, connected={"linear"})
    monkeypatch.setattr(routing, "_try_question_route", _real_question_route)
    monkeypatch.setattr("app.insights.resolve.classify_route",
                        lambda q, **k: AskIntent("qa", chart_ask="visual"))
    spec = ChartSpec(metric="issue_states", group_by=None, period="week", chart="line")
    monkeypatch.setattr("app.insights.resolve.classify_question",
                        lambda q, **k: AskIntent("chart", spec=spec))
    decision = routing.choose_agent("graph Linear issues", ORG, chart_from_words=True)
    assert (decision.reason, decision.chart_spec) == ("chart", spec)


def test_a_count_ask_is_carried_for_the_chart_button(monkeypatch):
    from app.insights.resolve import AskIntent

    _stub(monkeypatch, connected={"linear", "notion"}, scores={"notion": 0.8})
    monkeypatch.setattr(routing, "_try_question_route", _real_question_route)
    monkeypatch.setattr("app.insights.resolve.classify_route",
                        lambda q, **k: AskIntent("qa", chart_ask="count"))
    decision = routing.choose_agent("how many issues did we close?", ORG)
    assert (decision.agent_key, decision.chart_ask) == ("notion", "count")
