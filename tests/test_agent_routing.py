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


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    # Cosine-routing tests must not spend a live classifier call.
    monkeypatch.setattr(routing, "_try_insights_route", lambda *a, **k: None)


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


def test_a_chart_intent_beats_the_only_connected_source(monkeypatch):
    """The metric's provider is the connector. Cosine would send this to Linear
    RAG, which would invent a total from issue text."""
    _stub(monkeypatch, connected={"linear"})
    spec = ChartSpec(
        metric="issues_completed",
        group_by="subject",
        period="month",
        chart="pie",
    )
    monkeypatch.setattr(
        routing,
        "_try_insights_route",
        lambda q, c: routing.RoutingDecision(INSIGHTS_KEY, "chart", chart_spec=spec),
    )

    decision = routing.choose_agent("share of completed work by team", ORG)

    assert decision.agent_key == INSIGHTS_KEY
    assert decision.reason == "chart"
    assert decision.chart_spec == spec


def test_an_uncountable_visual_still_routes_to_insights(monkeypatch):
    """RAG must not invent a number when the classifier wanted a chart."""
    _stub(monkeypatch, connected={"linear", "notion"})
    monkeypatch.setattr(
        routing,
        "_try_insights_route",
        lambda q, c: routing.RoutingDecision(
            INSIGHTS_KEY,
            "chart-refuse",
            chart_refusal="I can't chart that from your connected apps.",
        ),
    )

    decision = routing.choose_agent("chart of team happiness", ORG)

    assert decision.agent_key == INSIGHTS_KEY
    assert decision.reason == "chart-refuse"
    assert decision.chart_refusal is not None
