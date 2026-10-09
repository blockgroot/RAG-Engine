# RCA: why broad questions stopped improving

*9 October 2026 · Handbook, Benchmark 2 questions, local benchmark database*

## Summary

Handbook's search is meant to be **hybrid**: a meaning-based (vector) search plus a keyword search, combined. **The keyword half returns nothing for 93% of questions**, so search has effectively been vector-only. Every accuracy change since Benchmark 1 (bigger pool, reranker titles, contextual notes, deep reading) worked on a candidate list that keyword matching never fed. That is why broad questions stopped improving.

## What we saw

On the 15 broad questions Handbook kept failing, even with deep reading:

- **40% of the right documents never reached the answer** (35 of 58 did).
- Reading more of each document doubled the facts found (23% to 46%) but **did not find more of the right documents** (59–60% in every variant).
- A bigger pool (80), titles for the reranker and contextual notes also found **no more right documents**.

## Root cause

The keyword search (`app/vectorstore/pgvector_store.py`, `keyword_search`) uses Postgres `websearch_to_tsquery`. That requires **every** word of the question to appear in a single piece of a document.

| Check (all 200 benchmark questions) | Result |
|---|---|
| Questions where keyword search matched **nothing** | **186 of 200 (93%)** |
| Questions where it matched anything after the fix | 200 of 200 |

A natural question such as "List all customers who reported X during Y" never has all its words in one piece, so the keyword leg is empty and the result is vector-only. Short queries ("leave policy") still match, which is why this went unnoticed. **This affects production too:** the same code serves real users, and longer questions get vector-only search.

## Why it matters most for broad questions

- Vector search ranks by overall meaning. A generic question ("list all customers…") is not close in meaning to each specific document (each customer's case study), so those rank 74th to 413th and miss the 40 candidates.
- Keyword search is what catches exact shared words (customer names, "case study", project codes, ADR numbers). With it switched off in practice, nothing pulls those documents in.

## Measured effect of a working keyword leg

The keyword leg now matches **any** of the question's words, ranked by relevance, then BM25 as before, and is combined with vector search as it always was. Measured through the real search code (search only, no AI calls):

| Right documents in the 40 candidates | Before | After |
|---|---|---|
| 15 failing broad questions | 76% | **98%** |
| All 200 questions | 86% | **94%** |
| "List everything" questions | 66% | **83%** |
| Semantic questions | 75% | **100%** |
| Project questions | 98% | 98% |

The keyword step now scores its top 200 matches instead of 2,000: that kept the most right documents (94%, against 92% at 500) and is faster. Search's first stage takes about 250 ms, against about 70 ms when the keyword leg was empty.

## Other losses found

1. **Found but not used:** 11 of the 58 right documents were among the 40 candidates but did not reach the answer (the reranker or the top-5 selection dropped them). This is the next limit after search is fixed.
2. **Crowding (tested, ruled out):** one long document can take up to 14 of the 40 candidate places. After the keyword fix, capping each document at 3, 5 or 8 places reached no more right documents than today (93.6% with no cap, 92.3–93.1% with one), so no cap was added.
3. **Big-picture questions** have no expected documents in the dataset. Their failures are about writing a summary, not search, so this fix will not move them.

## Why it was never noticed

- The keyword search has required every word since it was added (Phase 6, 22 July).
- Its only test searched for two words ("ZephyrCare Platinum"), which pass either way.
- An empty keyword leg fails silently: combining the two searches falls back to vector-only with no error or log.
- Benchmarks scored final answers, not what each half of search found, so every loss looked like a ranking or reading problem.

A new test (`test_keyword_search_finds_a_long_natural_question`) fails on the old query.

## Fix plan

1. Done: the keyword leg matches any of the question's words, ranked by relevance (the existing BM25 re-ranking stays). No AI calls, no new dependency.
2. Re-run the 15 failing questions, then all 150, against Benchmark 2 and Onyx.
3. Done: the selection now sees the whole reranked pool instead of the top 10 pieces (62% to 66% of right documents for many-document questions, simple questions unchanged), and neighbouring pieces only use room left after every selected document.
