# Closing the gap with Onyx: summary

*8 October 2026 · Handbook only, on Benchmark 2's questions, model (gpt-oss-120b) and reviewer (Gemma 4)*

## Kept: version C

All are settings, off by default, with values chosen from benchmark data. None adds an AI call.

| Change | What it does |
|---|---|
| Wide reads | When several documents score well, adds the best passage from each, up to 10 |
| Bigger search pool | The reranker considers 40 candidates instead of 16 |
| Partial answers | Answers the part the documents cover, instead of "I don't know" |
| Newer document wins | When documents disagree, uses the newer one |
| Weak-result cutoff (0.2) | Drops passages far below the best one |
| Rephrasing off | Removes one AI call per question; it didn't improve accuracy |

**Result (150 questions):**

| | Handbook before | Version C | Onyx |
|---|---|---|---|
| Key answer right | 65.3% | **70.0%** | 64.7% |
| Tokens per question | 4.2k | **3.7k** | 69k |
| AI calls per question | 3.6 | **2.8** | 12.1 |

It still says "I don't know" on all 15 unanswerable questions.

## Tried and dropped

| Idea | Result |
|---|---|
| "Answer every item" rule on wide reads | No gain |
| Ranking whole documents instead of pieces | Reached fewer right documents |
| Reranker sees document titles | No gain on the failed questions |
| AI-written notes on each piece (contextual retrieval) | No gain; sometimes worse search |

## The remaining gap, and the fix that works

Onyx still leads on broad questions: whole projects, "list everything" and summaries.

- **Cause:** Onyx reads a few documents deeply (45k–150k tokens per question). Handbook read small pieces of them.
- **Fix:** read the top 3 documents whole (`RAG_NEIGHBOR_TOP_DOCS=3`, `RAG_NEIGHBOR_CHUNKS=8`, `RAG_MAX_CONTEXT_CHARS=30000`).

| 15 questions Handbook was failing | Key answer right | Facts found |
|---|---|---|
| Version C (asked again) | 1/15 | 21% |
| **With deep reading** | **6/15** | **47%** |

**Caveat:** tokens rise from about 3.6k to 8.2k per question. That's still about 8× cheaper than Onyx, with no extra AI calls and the same speed.

## Next

1. Run deep reading on questions Handbook already answers correctly, to check nothing breaks.
2. If simple questions suffer or cost too much, use deep reading only on wide reads (broad questions).
3. Run the final configuration on all 150 questions and compare with Onyx.
