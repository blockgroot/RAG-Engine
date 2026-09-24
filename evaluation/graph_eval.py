"""Multi-hop retrieval eval for the knowledge graph (Second Brain step 1.7).

Answers the one question that decides whether ``GRAPH_RETRIEVAL_ENABLED`` goes
on in production: **does the graph put the right document in front of the
model when words alone do not?** No LLM, deterministic: each case is run
through the real hybrid retriever twice -- graph list OFF, then ON -- and
scored by whether the expected document reaches the final ``top_k``.

The corpus is built so the answer is connected to the question only through
the graph. Retrieval by similarity can still find it by luck, and that is
fine: the report shows both columns, and the graph earns its switch only where
ON finds what OFF missed, with nothing that OFF found lost.

The golden policy corpus is seeded alongside as distractors, so a five-document
corpus cannot put everything in the top five by default.

Run: ``python -m evaluation.graph_eval`` (needs DATABASE_URL and an embedder).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config.settings import GraphSettings
from app.db.connection import get_connection
from app.ingestion.chunking import chunk_text
from app.ingestion.preprocessing import preprocess
from app.rag.retrieval import HybridRetriever
from app.sources.meta import build_meta, container, editor_key, extract_links, link, merge_meta, person

from .golden_set import CORPUS as DISTRACTORS

PRIYA = "priya@graph-eval.example.com"


@dataclass(frozen=True)
class GraphDoc:
    provider: str
    external_id: str
    title: str
    text: str
    meta: dict | None


GRAPH_CORPUS: list[GraphDoc] = [
    GraphDoc(
        "linear", "issue-142", "ENG-142 - Token refresh fails on Safari",
        "Linear issue ENG-142. status In Progress. Refresh tokens are dropped when "
        "Safari blocks third-party cookies, so sessions end after an hour.",
        build_meta(
            people=[person("linear", role="assignee", external_id="lin-priya", email=PRIYA, name="Priya")],
            links=[link("github", "pr:acme/api#14", "https://github.com/acme/api/pull/14")],
            containers=[container("linear", "team", "team-auth", "Auth")],
        ),
    ),
    GraphDoc(
        "google", "auth-design", "Auth design notes",
        "Session lifetime is fifteen minutes for the access credential and thirty days "
        "for the long-lived one. Rotation happens on every use; reuse revokes the family.",
        build_meta(
            people=[person("google", role="editor", external_id="perm-priya", email=PRIYA, name="Priya")],
            containers=[container("google", "folder", "folder-eng", "Engineering")],
        ),
    ),
    GraphDoc(
        "slack", "C-AUTH:1700000000.000100", "#auth: rollout",
        "Channel: #auth\n[10:02] Rahul: rolled the fix out to staging, watching the error "
        "rate before prod. Details in https://linear.app/acme/issue/ENG-142/token-refresh",
        build_meta(
            people=[person("slack", role="author", external_id="U-RAHUL", name="Rahul")],
            containers=[container("slack", "channel", "C-AUTH", "auth")],
        ),
    ),
    GraphDoc(
        "notion", "oncall-page", "On-call handbook",
        "Paging rotates weekly. Escalate to the platform lead after thirty minutes "
        "without acknowledgement.",
        build_meta(people=[person("notion", role="editor", external_id="n-sam", name="Sam")]),
    ),
]


@dataclass(frozen=True)
class GraphEvalCase:
    id: str
    question: str
    expected_title: str
    rationale: str


GRAPH_EVAL_CASES: list[GraphEvalCase] = [
    GraphEvalCase(
        id="assignee-other-work",
        question="What else has the person who owns ENG-142 written about sessions?",
        expected_title="Auth design notes",
        rationale=(
            "Two hops through one person: issue -assigned_to- Priya (Linear) -same_person- "
            "Priya (Drive) -edited- the design doc, which never mentions ENG-142."
        ),
    ),
    GraphEvalCase(
        id="discussion-of-issue",
        question="Where was ENG-142 discussed and what happened after the fix?",
        expected_title="#auth: rollout",
        rationale="One hop: the Slack thread references the issue by URL.",
    ),
    GraphEvalCase(
        id="direct-lookup",
        question="Why does token refresh fail on Safari?",
        expected_title="ENG-142 - Token refresh fails on Safari",
        rationale="Control: similarity alone finds it; the graph must not push it out.",
    ),
]


@dataclass(frozen=True)
class GraphEvalResult:
    case: GraphEvalCase
    found_off: bool
    found_on: bool
    graph_hits: int


def seed_graph_corpus(store, embedder, name: str) -> str:
    """Create an org, a member for Priya, the corpus and its graph; return org_id."""
    from app.auth.users import invite_member
    from app.graph import builder

    org_id = store.create_organization(name)
    invite_member(PRIYA, org_id)  # her Linear and Drive identities link by email
    for title, text in DISTRACTORS:
        chunks = chunk_text(preprocess(text))
        store.add_document(org_id, title, chunks, embedder.embed(chunks))
    by_provider: dict[str, list[str]] = {}
    for doc in GRAPH_CORPUS:
        chunks = chunk_text(preprocess(doc.text)) or [doc.text]
        # Exactly what `ingestion.pipeline._doc_meta` stores: the adapter's
        # record plus every link found in the body.
        meta = merge_meta(doc.meta, links=extract_links(doc.text)) or {}
        store.upsert_source_document(
            org_id, provider=doc.provider, external_id=doc.external_id, title=doc.title,
            chunks=chunks, embeddings=embedder.embed(chunks), source_meta=meta,
            editor_key=editor_key(meta), last_modified=datetime.now(timezone.utc),
        )
        by_provider.setdefault(doc.provider, []).append(doc.external_id)
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO activity_facts (org_id, provider, kind, actor, actor_key, subject,
                                        occurred_at, external_id)
            VALUES (%s::uuid, 'github', 'pr_reviewed', 'rahul', 'github:rahul', 'acme/api',
                    now(), 'acme/api#14:rahul')
            """,
            (org_id,),
        )
    for provider, ids in by_provider.items():
        builder.build_documents(org_id, None, provider, ids)
    builder.build_facts(org_id, None)
    return org_id


def _found(retriever: HybridRetriever, embedder, org_id: str, case: GraphEvalCase):
    qvec = embedder.embed([case.question])[0]
    result = retriever.retrieve(org_id, case.question, qvec)
    titles = {(h.document_title or "").strip() for h in result.hits}
    return case.expected_title in titles, result.graph_hits


def run_graph_suite(store, embedder, org_id: str, *, reranker=None) -> list[GraphEvalResult]:
    """Every case with the graph list OFF and ON, same retriever otherwise."""
    off = HybridRetriever(store, reranker=reranker, graph_settings=GraphSettings(retrieval_enabled=False))
    on = HybridRetriever(store, reranker=reranker, graph_settings=GraphSettings(retrieval_enabled=True))
    results = []
    for case in GRAPH_EVAL_CASES:
        found_off, _ = _found(off, embedder, org_id, case)
        found_on, hits = _found(on, embedder, org_id, case)
        results.append(GraphEvalResult(case, found_off, found_on, hits))
    return results


def verdict(results: list[GraphEvalResult]) -> str:
    """``enable`` only when ON gains somewhere and loses nowhere."""
    gained = sum(1 for r in results if r.found_on and not r.found_off)
    lost = sum(1 for r in results if r.found_off and not r.found_on)
    if lost:
        return f"keep off: the graph lost {lost} case(s) similarity found"
    if not gained:
        return "keep off: no gain over similarity alone"
    return f"enable: +{gained} case(s), nothing lost"


def format_report(results: list[GraphEvalResult]) -> str:
    lines = ["| case | off | on | graph hits |", "|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r.case.id} | {'✓' if r.found_off else '✗'} | {'✓' if r.found_on else '✗'} "
            f"| {r.graph_hits} |"
        )
    lines.append("")
    lines.append(f"**Verdict:** {verdict(results)}")
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - a CLI
    from app.embeddings import build_embedding_provider
    from app.vectorstore import build_vector_store

    store = build_vector_store()
    embedder = build_embedding_provider()
    org_id = seed_graph_corpus(store, embedder, f"GraphEval-{uuid.uuid4().hex[:8]}")
    try:
        print(format_report(run_graph_suite(store, embedder, org_id)))
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))


if __name__ == "__main__":  # pragma: no cover
    main()
