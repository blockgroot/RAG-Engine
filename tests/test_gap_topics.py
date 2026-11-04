"""Unanswered questions grouped by meaning, and who hears about them.

The embedder is a fake that places each question on a fixed direction:
paraphrases near one axis (cosine ~0.96), unrelated topics on orthogonal axes
(cosine 0). The model is a fake that knows which questions MEAN the same
(``MEANING``), so a pair the embedder puts close together but that asks for
different things (annual vs parental leave) tests the model's veto.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import feedback
from app.auth import create_admin, create_session_token
from app.auth.users import invite_member
from app.db import get_connection
from app.feedback import topics
from .conftest import requires_db

pytestmark = requires_db

DIM = 1024
#: question -> (topic axis, nudge axis). Same first number = same meaning.
AXES = {
    "How do I claim travel expenses?": (0, None),
    "what's the reimbursement process for trips": (0, 7),
    "expense claim form?": (0, 8),
    "where is the expense policy": (0, 9),
    "What is our policy on flights?": (1, None),
    "Who approves the quarterly budget?": (2, None),
    "Slack messages per person": (3, None),
    "How many days of annual leave do I get?": (4, None),
    "What is the parental leave policy?": (4, 10),
}
#: What each question really asks for; the fake model reads this.
MEANING = {q: axis for q, (axis, _) in AXES.items()}
MEANING["What is the parental leave policy?"] = 99


class FakeModel:
    """Names the offered topic with the same MEANING, as the real one is asked to."""

    def __init__(self, reply=None):
        self.calls = 0
        self.reply = reply

    def generate(self, prompt, *, max_tokens=None):
        self.calls += 1
        if self.reply is not None:
            return self.reply
        question = prompt.split("<<<UNTRUSTED_QUESTION>>>\n")[1].split("\n")[0]
        listed = prompt.split("<<<UNTRUSTED_EARLIER_QUESTIONS>>>\n")[1].split("\n<<<END")[0]
        for line in listed.splitlines():
            n, label = line.split(". ", 1)
            if MEANING[label] == MEANING[question]:
                return '{"topic": %s}' % n
        return '{"topic": 0}'


class DownModel:
    def generate(self, prompt, *, max_tokens=None):
        raise RuntimeError("429 quota")


class FakeEmbedder:
    def embed(self, texts):
        out = []
        for text in texts:
            axis, nudge = AXES[text]
            v = np.zeros(DIM)
            v[axis] = 1.0
            if nudge is not None:
                v[nudge] = 0.3
            out.append((v / np.linalg.norm(v)).tolist())
        return out


class BrokenEmbedder:
    def embed(self, texts):
        raise RuntimeError("Insufficient account balance")


@pytest.fixture
def org(store, org_cleanup):
    org_id = store.create_organization(f"Gaps {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"gaps-admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    people = [invite_member(f"gaps-{i}-{uuid.uuid4().hex[:8]}@example.com", org_id) for i in range(3)]
    topics._quiet_until.pop(org_id, None)
    return SimpleNamespace(id=org_id, admin=admin, people=people)


def _ask(org_id, who, question, *, workspace_id=None, agent="notion", score=None):
    feedback.record_gap(org_id=org_id, question=question, user_id=who.id, agent=agent,
                        workspace_id=workspace_id, gate_score=score)


def _group(org_id, model=None):
    topics.assign_topics(org_id, embedder=FakeEmbedder(), llm=model or FakeModel())


def test_paraphrases_are_one_topic_that_alerts_at_three_different_people(org):
    a, b, c = org.people
    _ask(org.id, a, "How do I claim travel expenses?")
    _ask(org.id, a, "expense claim form?")                 # same person again
    _ask(org.id, b, "what's the reimbursement process for trips", score=0.31)
    _ask(org.id, a, "What is our policy on flights?")
    _group(org.id)

    [expenses, flights] = topics.list_topics(org.id, None)
    assert expenses.label == "How do I claim travel expenses?"
    assert expenses.missing_people == 2 and not expenses.alerting
    assert set(expenses.questions) == {"How do I claim travel expenses?", "expense claim form?",
                                       "what's the reimbursement process for trips"}
    assert expenses.best_score == pytest.approx(0.31, abs=1e-4)
    assert flights.missing_people == 1 and flights.best_score is None

    _ask(org.id, c, "where is the expense policy")
    _group(org.id)
    top = topics.list_topics(org.id, None)[0]
    assert (top.id, top.missing_people, top.alerting) == (expenses.id, 3, True)


def test_two_people_marking_answers_wrong_alerts(org):
    a, b, _ = org.people
    for who in (a, b):
        with get_connection() as conn:
            conv = conn.execute("INSERT INTO conversations (org_id, user_id) VALUES (%s, %s) "
                                "RETURNING id::text", (org.id, who.id)).fetchone()[0]
        feedback.record_rating(org_id=org.id, user_id=who.id, conversation_id=conv,
                               question="Who approves the quarterly budget?", rating=-1,
                               answer="The CFO.", reason="out_of_date", comment="It's the board now")
    _group(org.id)
    [budget] = topics.list_topics(org.id, None)
    assert (budget.wrong_people, budget.missing_people, budget.alerting) == (2, 0, True)
    assert (budget.wrong_answer, budget.wrong_reason) == ("The CFO.", "out_of_date")


def test_a_refused_chart_and_a_good_answer_are_never_topics(org):
    a, _, _ = org.people
    _ask(org.id, a, "Slack messages per person", agent=feedback.CHART_AGENT)
    with get_connection() as conn:
        conv = conn.execute("INSERT INTO conversations (org_id, user_id) VALUES (%s, %s) "
                            "RETURNING id::text", (org.id, a.id)).fetchone()[0]
    feedback.record_rating(org_id=org.id, user_id=a.id, conversation_id=conv,
                           question="What is our policy on flights?", rating=1)
    _group(org.id)
    assert topics.list_topics(org.id, None) == []


def test_a_spaces_topics_stay_in_that_space_and_companies_stay_apart(org, store, org_cleanup):
    from app.workspaces.store import create_workspace

    a, b, _ = org.people
    space = create_workspace(org.id, "Finance", a.id)
    _ask(org.id, a, "How do I claim travel expenses?")
    _ask(org.id, b, "expense claim form?", workspace_id=space)
    other = store.create_organization(f"Gaps other {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other)
    stranger = invite_member(f"gaps-x-{uuid.uuid4().hex[:8]}@example.com", other)
    _ask(other, stranger, "where is the expense policy")
    _group(org.id)
    _group(other)

    [company] = topics.list_topics(org.id, None)
    [in_space] = topics.list_topics(org.id, space)
    assert company.questions == ["How do I claim travel expenses?"]
    assert in_space.questions == ["expense claim form?"] and in_space.id != company.id
    assert [t.questions for t in topics.list_topics(other, None)] == [["where is the expense policy"]]


def test_handled_hides_a_topic_until_it_is_asked_again(org):
    a, b, _ = org.people
    _ask(org.id, a, "How do I claim travel expenses?")
    _group(org.id)
    [t] = topics.list_topics(org.id, None)
    assert not topics.mark_handled(org.id, str(uuid.uuid4()), t.id)    # not that scope
    assert topics.mark_handled(org.id, None, t.id)
    assert topics.list_topics(org.id, None) == []

    _ask(org.id, b, "expense claim form?")
    _group(org.id)
    [again] = topics.list_topics(org.id, None)
    assert (again.id, again.missing_people, again.questions) == (t.id, 1, ["expense claim form?"])


def test_an_embedding_failure_leaves_questions_waiting_and_backs_off(org):
    a, _, _ = org.people
    _ask(org.id, a, "How do I claim travel expenses?")
    topics.assign_topics(org.id, embedder=BrokenEmbedder(), llm=FakeModel())  # never raises
    assert topics.list_topics(org.id, None) == []
    _group(org.id)                                                     # backing off
    assert topics.list_topics(org.id, None) == []
    topics._quiet_until.pop(org.id)
    _group(org.id)
    assert len(topics.list_topics(org.id, None)) == 1


def test_the_model_keeps_a_close_but_different_subject_apart(org):
    a, b, _ = org.people
    _ask(org.id, a, "How many days of annual leave do I get?")
    _ask(org.id, b, "What is the parental leave policy?")      # cosine ~0.96 to it
    model = FakeModel()
    _group(org.id, model)
    assert model.calls == 1                                     # offered, and refused
    assert len(topics.list_topics(org.id, None)) == 2


def test_no_similar_topic_costs_no_model_call(org):
    a, b, _ = org.people
    _ask(org.id, a, "How do I claim travel expenses?")
    _ask(org.id, b, "Who approves the quarterly budget?")
    model = FakeModel()
    _group(org.id, model)
    assert model.calls == 0 and len(topics.list_topics(org.id, None)) == 2


@pytest.mark.parametrize("reply", ["no idea", '{"topic": 7}', '{"topic": true}', '{"topic": "1"}'])
def test_a_reply_that_names_no_offered_topic_starts_a_new_one(org, reply):
    a, b, _ = org.people
    _ask(org.id, a, "How do I claim travel expenses?")
    _ask(org.id, b, "expense claim form?")
    _group(org.id, FakeModel(reply))
    assert len(topics.list_topics(org.id, None)) == 2


def test_a_model_outage_leaves_the_question_waiting(org):
    a, b, _ = org.people
    _ask(org.id, a, "How do I claim travel expenses?")
    _ask(org.id, b, "expense claim form?")
    _group(org.id, DownModel())
    [only] = topics.list_topics(org.id, None)
    assert only.questions == ["How do I claim travel expenses?"]  # the second waits
    topics._quiet_until.pop(org.id)
    _group(org.id)
    [merged] = topics.list_topics(org.id, None)
    assert merged.missing_people == 2


@pytest.fixture
def client(monkeypatch):
    from app.api.main import create_app

    import app.api.feedback as feedback_api
    import app.api.notifications as notifications_api

    monkeypatch.setattr(feedback_api, "assign_topics_soon", lambda org_id: _group(org_id))
    monkeypatch.setattr(notifications_api, "assign_topics_soon", lambda org_id: _group(org_id))
    return TestClient(create_app())


def _cookies(user):
    return {"session": create_session_token(user)}


def test_the_bell_tells_the_admin_and_the_space_owner_only_their_own(client, org):
    from app.workspaces.store import create_workspace, invite_member as invite_to_space

    a, b, c = org.people
    space = create_workspace(org.id, "Finance", a.id)                 # a owns it
    invite_to_space(space, org.id, a.id, b.email)
    _ask(org.id, a, "How do I claim travel expenses?", workspace_id=space)
    # One person's miss notifies nobody, so the page the bell opens shows nothing.
    assert client.get(f"/workspaces/{space}/feedback", cookies=_cookies(a)).json()["topics"] == []
    for who, q in ((a, "How do I claim travel expenses?"), (b, "expense claim form?"),
                   (c, "where is the expense policy")):
        _ask(org.id, who, q)
        _ask(org.id, who, q, workspace_id=space)

    admin_bell = client.get("/notifications", cookies=_cookies(org.admin)).json()["items"]
    [gap] = [i for i in admin_bell if i["kind"] == "gap"]
    assert gap["scope"] == "Company" and gap["href"].startswith("/admin/feedback#topic-")
    assert gap["title"].startswith("3 people couldn")

    owner_bell = client.get("/notifications", cookies=_cookies(a)).json()["items"]
    [mine] = [i for i in owner_bell if i["kind"] == "gap"]
    assert mine["scope"] == "Finance" and mine["href"].startswith(f"/workspaces/{space}/feedback#topic-")
    member_bell = client.get("/notifications", cookies=_cookies(b)).json()["items"]
    assert not [i for i in member_bell if i["kind"] == "gap"]

    # A space's questions are its owner's: a member is refused, not shown nothing.
    assert client.get(f"/workspaces/{space}/feedback", cookies=_cookies(b)).status_code == 403
    [topic] = client.get(f"/workspaces/{space}/feedback", cookies=_cookies(a)).json()["topics"]
    assert topic["alerting"] and topic["missing_people"] == 3
    assert client.get("/admin/feedback", cookies=_cookies(a)).status_code == 403
    r = client.post(f"/workspaces/{space}/feedback/topics/{topic['id']}/handled", cookies=_cookies(a))
    assert r.status_code == 200
    assert client.get(f"/workspaces/{space}/feedback", cookies=_cookies(a)).json()["topics"] == []
    r = client.post(f"/admin/feedback/topics/{topic['id']}/handled", cookies=_cookies(org.admin))
    assert r.status_code == 404                                        # not a company topic
