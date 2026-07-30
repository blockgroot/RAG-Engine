#!/usr/bin/env python3
"""Misspelling / informal-phrasing retrieval-rank harness (Phase 17).

Honest scope
------------
ARCHITECTURE.md records that ``protien suppliments reimbersed`` ranked the
answer chunk at ~#18–24 on the *live Notion-backed Acme corpus* (larger /
noisier than the golden-set CORPUS). This harness does **not** claim to
reproduce that exact measurement: it seeds the deterministic golden CORPUS
plus a wellness page, optionally padded with distractor docs to *approximate*
mid-pool degradation.

What it *does* prove:
- spelling correction fires toward corpus terms;
- clean questions are not corrupted;
- on a padded corpus, typo rank can sit mid-pool and improve after normalize.

Usage:
    .venv/bin/python scripts/measure_query_normalization.py
    .venv/bin/python scripts/measure_query_normalization.py --noisy
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.config.settings import QueryNormSettings
from app.db import close_pool, get_connection
from app.embeddings import build_embedding_provider
from app.ingestion.chunking import chunk_text
from app.ingestion.preprocessing import preprocess
from app.rag.query_normalize import CorpusSpellNormalizer
from app.vectorstore import build_vector_store
from evaluation.golden_set import GOLDEN_CASES
from evaluation.harness import seed_corpus


VARIANTS: list[tuple[str, str, str, str | None]] = [
    (
        "annual-leave-days",
        "How many days of paid annual leave do full-time employees get per year?",
        "how meny days of paid anual leave do full-time employes get per year?",
        None,
    ),
    (
        "sick-leave-days",
        "How many paid sick days do employees get per year?",
        "how meny paid sic days do employees get per year?",
        None,
    ),
    (
        "expense-limit",
        "What is the reimbursement limit for business travel expenses per trip?",
        "what is the reimbersement limit for buisness travel expences per trip?",
        None,
    ),
    (
        "health-plan",
        "What health and dental insurance plan does the company provide?",
        "what helth and dentel insurance plan does the company provide?",
        None,
    ),
    (
        "remote-work",
        "How many days per week can employees work remotely?",
        "how meny days per weak can employes work remotley?",
        None,
    ),
    (
        "carry-over",
        "How many unused annual leave days can be carried over into the next year?",
        "how meny unused anual leave days can be carryed over into the next year?",
        None,
    ),
    (
        "protein-wellness",
        "Are protein supplements reimbursed under the wellness allowance?",
        "Are protien suppliments reimbersed under the wellnes alowance?",
        "protein supplements",
    ),
]

WELLNESS_DOC = """
# Health & Wellness Allowance
Employees may purchase protein supplements and other health-related products
with the annual wellness allowance. Claims are reimbursed through the HR portal
with a receipt. The wellness allowance does not cover gym memberships.
"""

# Distractors: topical neighbors that crowd the embedding space without answering
# the protein question — closer to a multi-page Notion wiki than golden CORPUS.
NOISE_DOCS = [
    (
        f"HR Misc {i}",
        f"""# HR Handbook Section {i}
Office procedures, desk booking, visitor badges, parking permits, cafeteria
hours, laptop refresh cycles, security badge replacement, compliance training
module {i}, expense coding tips, travel booking tips, wellness *program*
enrollment (not the purchase allowance), gym partnership discounts, and
manager FAQ number {i}.
""",
    )
    for i in range(1, 51)
]


def _rank_and_score(hits: list, *, must_contain: str) -> tuple[int | None, float | None]:
    needle = must_contain.lower()
    for i, h in enumerate(hits, start=1):
        if needle in h.content.lower():
            return i, float(h.score)
    return None, None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--noisy",
        action="store_true",
        help="Pad with ~50 distractor docs to approximate mid-pool degradation",
    )
    args = parser.parse_args()

    store = build_vector_store()
    embedder = build_embedding_provider()
    org_id = seed_corpus(store, embedder, f"QueryNorm-{uuid.uuid4().hex[:8]}")

    wellness_chunks = chunk_text(preprocess(WELLNESS_DOC))
    store.add_document(
        org_id,
        "Health & Wellness Allowance",
        wellness_chunks,
        embedder.embed(wellness_chunks),
    )
    if args.noisy:
        for title, body in NOISE_DOCS:
            chunks = chunk_text(preprocess(body))
            store.add_document(org_id, title, chunks, embedder.embed(chunks))

    texts = store.list_chunk_texts(org_id)
    normalizer = CorpusSpellNormalizer(
        QueryNormSettings(enabled=True, max_edit_distance=1, min_word_length=4)
    )
    normalizer.clear_cache()

    facts = {
        c.id: (c.expected_facts[0] if c.expected_facts else "") for c in GOLDEN_CASES
    }

    mode = "noisy" if args.noisy else "golden+wellness"
    print(
        f"org={org_id} chunks={len(texts)} mode={mode}\n"
        "NOTE: ARCHITECTURE.md #18–24 was on live Notion Acme data — this harness "
        "does not replay that corpus; --noisy only approximates crowding."
    )
    print(
        f"{'case':<18} {'clean_rank':>10} {'typo_before':>12} "
        f"{'typo_after':>11} {'spelling_ok':>12} {'normalized_query'}"
    )
    print("-" * 120)

    clean_ok = 0
    improved = 0
    spelling_ok = 0
    typo_tokens = {
        "meny", "anual", "employes", "sic", "reimbersement", "buisness",
        "expences", "helth", "dentel", "weak", "remotley", "carryed",
        "protien", "suppliments", "reimbersed", "wellnes", "alowance",
    }
    for case_id, clean_q, typo_q, fact_override in VARIANTS:
        fact = fact_override or facts.get(case_id, "")
        if not fact:
            continue

        clean_vec = embedder.embed([clean_q])[0]
        clean_rank, clean_score = _rank_and_score(
            store.query(org_id, clean_vec, top_k=30), must_contain=fact
        )

        typo_vec = embedder.embed([typo_q])[0]
        before_rank, before_score = _rank_and_score(
            store.query(org_id, typo_vec, top_k=30), must_contain=fact
        )

        fixed_q = normalizer.normalize(typo_q, org_id, texts)
        fixed_vec = embedder.embed([fixed_q])[0]
        after_rank, after_score = _rank_and_score(
            store.query(org_id, fixed_vec, top_k=30), must_contain=fact
        )

        clean_fixed = normalizer.normalize(clean_q, org_id, texts)
        if clean_fixed != clean_q:
            print(f"WARN clean question changed for {case_id}: {clean_fixed!r}")

        fixed_low = fixed_q.lower()
        spell_ok = not any(t in fixed_low.split() for t in typo_tokens)
        if spell_ok:
            spelling_ok += 1
        if clean_rank == 1:
            clean_ok += 1
        if before_rank is not None and after_rank is not None and after_rank < before_rank:
            improved += 1
        elif before_rank is None and after_rank is not None:
            improved += 1

        def _fmt(r, s):
            if r is None:
                return "None"
            return f"{r}@{s:.2f}" if s is not None else str(r)

        print(
            f"{case_id:<18} {_fmt(clean_rank, clean_score):>10} "
            f"{_fmt(before_rank, before_score):>12} "
            f"{_fmt(after_rank, after_score):>11} {str(spell_ok):>12} {fixed_q!r}"
        )

    print("-" * 120)
    print(
        f"clean #1: {clean_ok}/{len(VARIANTS)}; "
        f"typo rank improved: {improved}/{len(VARIANTS)}; "
        f"typo tokens cleared: {spelling_ok}/{len(VARIANTS)}"
    )

    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        close_pool()
