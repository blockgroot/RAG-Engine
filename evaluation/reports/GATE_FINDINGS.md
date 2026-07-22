# Phase 7 — Part 3: Evidence about the confidence gate

> **This is a findings report, not a change.** The cosine-similarity gate
> (`RAG_SIMILARITY_THRESHOLD = 0.35`) is **unchanged** this phase. Below is what the
> golden-set run actually shows about it, and a recommendation for a *future*
> decision — to be made separately.

## Method

Every golden case records `top_score` — the best cosine similarity retrieved — and
whether the answer was grounded, refused by the prompt, or short-circuited by the
gate. The gate admits a question to the LLM only if `top_score ≥ 0.35`. Data is from
`evaluation/reports/latest.md` (gate scores are deterministic; they do not vary run
to run). Two independent CLAUDE.md layers are under test: **(1) the gate** (cheap
cosine filter) and **(2) the strict prompt** (fine "on-topic but not answered"
judgment).

## The numbers

| Band | Cases | `top_score` range |
|------|-------|-------------------|
| Answerable (grounded)         | 10 (+1 conversation) | **0.652 – 0.806** |
| Unanswerable (fixed fallback) | 4                    | **0.396 – 0.523** |
| External / web                | 2                    | 0.271 – 0.404 |

Threshold = **0.35**. Every answerable and every unanswerable case in the set
**cleared** the gate (lowest score anywhere = 0.396, above 0.35).

## Question 1 — Did the gate ever block a genuinely answerable question (a false negative)?

**No — zero false negatives.** All 10 answerable cases (and the conversation
follow-up) cleared the gate, the lowest at **0.652** — nearly double the 0.35
threshold. There is a wide safety margin; nothing answerable came close to being
blocked.

One answerable case (`sick-leave-days`) *did* return the fallback in one run — but
its `top_score` was 0.652 (cleared the gate), it retrieved its exact chunk
("Sick leave is 10 days per year…"), and re-running it in isolation answered
correctly **4/4 times**. So that refusal came from **downstream LLM/prompt variance
on the free endpoint, not the gate**. The gate did its job; the generation step had
a one-off blip (which is why the eval harness retries answerable cases once — see
`harness._RETRYABLE`).

## Question 2 — Did the gate let through questions the prompt then had to catch?

**Yes — all 4 unanswerable cases (100%), by design.** Every unanswerable question
cleared the 0.35 gate (0.396 – 0.523) and was then correctly refused by the strict
prompt, returning the fixed fallback:

| Case | `top_score` | Who refused |
|------|-------------|-------------|
| `dress-code`     | 0.396 | prompt |
| `401k-match`     | 0.432 | prompt |
| `gym-discount`   | 0.446 | prompt |
| `parental-leave` | 0.523 | prompt |

This includes the **`parental-leave`** case flagged in earlier manual testing: it is
topically adjacent to the leave policies, scores 0.523 (well above the gate), and is
caught only because the prompt refuses to answer from related-but-non-answering
context. This is precisely the split the two-layer design intends (CLAUDE.md §2/§4):
the gate is a cheap noise filter, the prompt makes the semantic judgment.

## Question 3 — Is the gate a bottleneck, or working as intended?

**Working as intended. It is not a bottleneck.** Evidence:

1. **No false negatives**, with a large margin (lowest answerable 0.652 vs 0.35).
2. The unanswerable cases here are *deliberately plausible* HR-adjacent questions;
   they score 0.40 – 0.52, not the ~0.30 of true noise (§4). The gate is **not meant**
   to catch these — the prompt is — and it correctly didn't. Nothing is mis-filed.
3. The only answerable failure observed was **generation variance**, downstream of
   the gate — so the gate is not the quality-limiting factor in this set.

There *is* an apparent gap in this run between the top unanswerable (0.523) and the
lowest answerable (0.652). It is tempting to raise the threshold into that gap
(~0.58) to reject unanswerables cheaply. **The evidence argues against it** (next
section), and it is exactly the tiny-sample trap CLAUDE.md §4 warns about.

## Recommendation (for a future, separate decision — NOT implemented here)

**Keep the gate at 0.35 and keep the two-layer design.** Specifically:

- **Do not raise the threshold toward the observed gap (~0.58).** The two lowest
  *legitimate* answerable cases — `sick-leave-days` (0.652) and `health-plan`
  (0.672) — sit only ~0.07–0.09 above such a threshold. A slightly harder-phrased
  but genuinely answerable question would then be blocked outright (a real false
  negative and a silent wrong answer), trading away today's zero-false-negative
  behavior just to reject cases the prompt already handles correctly. Bad trade on
  this evidence, and the sample is far too small to trust the gap (§4).
- **The gate is not where to invest next.** The one soft spot this eval surfaced is
  *answer-generation stability* (a policy-answerable question occasionally refused by
  the model/prompt on the free endpoint), not the gate. If anything, watch the strict
  prompt's over-refusal tendency and model determinism — via the golden set — before
  touching the gate.
- **If the corpus grows** and genuinely-irrelevant queries become common, the correct
  path is: (a) add production logging of the `top_score` distribution, (b) expand the
  golden set (especially true-noise and hard-answerable cases), (c) *re-measure* the
  bands, and only then consider a **small, golden-set-validated** threshold change —
  never one motivated by the current ~15-case sample. Any change must be regression-
  guarded by this same golden set + RAGAS.

**Bottom line:** the gate is doing exactly its intended, narrow job — a cheap first
filter for clearly-irrelevant content — and the strict prompt is doing the harder
semantic discrimination, as designed. No gate change is warranted by this evidence.
