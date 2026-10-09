# Closing Handbook's gaps: Benchmark 2 follow-up

*Handbook-only follow-up to Benchmark 2 · 8 October 2026*

Benchmark 2 found five reasons Handbook lost questions that Onyx got right. We built a fix for each one, with **no extra AI calls**. Every number the fixes use was chosen from benchmark data, not by hand.

We then re-ran Handbook on the same 150 test questions, with the same AI model (gpt-oss-120b) and the same reviewer (Gemma 4) as Benchmark 2, so the results compare directly with Benchmark 2's Handbook and Onyx.

## In short

- **The kept version (C) is more accurate, cheaper and faster than Benchmark 2's Handbook:**

  | | Benchmark 2 Handbook | Version C | Onyx |
  |---|---|---|---|
  | Key answer right | 65.3% | **70.0%** | 64.7% |
  | Tokens per question | 4,215 | **3,660** (13% fewer) | 69,466 |
  | AI calls per question | 3.6 | **2.8** | 12.1 |
  | Time to first word | 6.2 s | **4.5 s** | 11.2 s |

- **It stays honest.** It still said "I don't know" on all 15 questions with no answer in the documents.
- **Rephrasing the question before every search isn't worth its AI call,** so it is now off.
- **Broad questions are still the gap.** On whole-project questions, Handbook scored 4 of 15 against Onyx's 10. These changes didn't close that gap; it needs a different kind of fix (see next steps).



## The changes

All of these are settings, off by default. Turning one on changes nothing else.

| Change | Problem it targets (Benchmark 2) | Setting |
|---|---|---|
| **Read more documents when the answer is spread out.** If several documents score close to the best one, keep the usual 5 passages and add the best passage from each other strong document, up to 10. If one document is clearly best, nothing changes. | 17 lost questions needed 6–8 documents; Handbook read 2–5 | `RAG_WIDE_MAX_HITS=10`, `RAG_WIDE_DOC_RATIO=0.3`, `RAG_WIDE_PER_DOC=1` |
| **Search a bigger pool:** the reranker scores 40 candidates instead of 16, so those extra documents can be found. This uses the reranker only, not the AI that writes answers. | Many right documents ranked below 16th | `RETRIEVAL_CANDIDATE_POOL=40` |
| **Answer the part it knows.** If the documents cover only part of the question, answer that part and say what's missing. The confidence gate is unchanged. | 6 lost questions: the right document was there, but it said "I don't know" | `RAG_PARTIAL_RULE=true` |
| **Prefer the newer document** when two disagree, and say the older value changed. | 2 lost questions gave an outdated version as current | `RAG_CONFLICT_RULE=true` |
| **Drop weak results:** passages below 20% of the best one's score. | 5 lost questions used a detail from a nearby, wrong document | `RETRIEVAL_RERANK_MIN_RATIO=0.2` |
| **Rephrasing before every search: off.** The existing backup still rephrases when a search is weak. | It cost an AI call on every question | `RECOVERY_PROACTIVE=false` |

**How the wide-read rule decides:**

- **It never guesses from the question's wording.** The search scores show whether the answer is spread out.
- **A wide read always keeps the usual 5 passages,** so it can only reach more documents, never fewer.

**How the values were chosen:** a calibration script (`evaluation.erb.calibrate_wide`) recorded the reranker's score for 40 candidates on all 200 questions, then tested different values.

- **At a ratio of 0.3,** broad questions reach **65% of their right documents instead of 52%**, while 83% of one-document questions still read exactly 5 passages.
- **The 50 tuning questions and the 150 test questions gave nearly the same numbers,** so the values aren't tailored to one set.



## What we tested

Four versions of Handbook, on the 50 tuning questions and then the 150 test questions:

| Version | What it is |
|---|---|
| A | Benchmark 2's Handbook settings (tuning questions only, as a baseline) |
| B | All the changes above, with rephrasing still on |
| **C** | **All the changes above, rephrasing off (kept)** |
| D | C + 2 passages per extra document + an "answer every item" rule on wide reads |



## Results (150 test questions)

|                                       | Benchmark 2 Handbook | B     | **C**     | D     | Onyx   |
| ------------------------------------- | -------------------- | ----- | --------- | ----- | ------ |
| **Key answer right**                  | 65.3%                | 66.7% | **70.0%** | 69.3% | 64.7%  |
| Wins (correct + honest "I don't know") | 43.3%               | 46.0% | **48.7%** | 46.0% | 34.7%  |
| Required facts included               | 69.7%                | 72.0% | **73.3%** | 70.7% | 70.5%  |
| Wrongly said "I don't know"           | 9.3%                 | 6.0%  | 6.7%      | 5.3%  | 2.7%   |
| No-answer questions: said "I don't know" | 15/15             | 14/15 | **15/15** | 15/15 | 1/15   |
| Made-up claims                        | 10.7%                | 7.3%  | 11.3%     | 8.0%  | 47.3%  |
| AI tokens per question                | 4,215                | 4,151 | **3,660** | 3,739 | 69,466 |
| AI calls per question                 | 3.6                  | 3.5   | **2.8**   | 2.8   | 12.1   |
| Time to first word                    | 6.2 s                | 6.0 s | **4.5 s** | 4.6 s | 11.2 s |
| Found the right documents             | 82.8%                | 86.5% | **86.7%** | 85.7% | 78.5%  |

**On the 50 tuning questions,** the key answer was right on 68% (A), 78% (B), 74% (C) and 74% (D).



## Observations

1. **Version C is the best overall.** It's the most accurate, with fewer tokens, fewer AI calls and faster answers. It still refuses every question with no answer in the documents.
2. **The gain is real but modest.** Question by question, C fixed 14 of Benchmark 2 Handbook's answers and broke 7, a net gain of 7. A gain that size could still happen by chance about one time in five, so it isn't proven on its own. The tuning questions moved in the same direction.
3. **Rephrasing before every search isn't worth it.** Without it (C), Handbook was more accurate than with it (B), and it saves an AI call on every question.
4. **Handbook now finds more of the right documents:** 86.7% against 82.8%. On some broad questions it went from 4 of 8 documents to all 8.
5. **"Answer the part it knows" works.** Wrongful "I don't know" answers fell from 9.3% to 6.7%, with no loss of honesty on questions that have no answer.
6. **Longer answers and more passages per document (D) didn't help.** D matched C overall and didn't improve broad questions, so its "answer every item" rule was removed from the code.
7. **Broad questions are still the gap.**

   | Question type | Handbook (C) | Onyx |
   |---|---|---|
   | Whole project | 4/15 | **10/15** |
   | Big-picture summary | 1/8 | **4/8** |
   | "List everything" | 5/15 | **7/15** |

   - Handbook now finds most of the right documents, but its answers miss details deeper inside them.
   - About 18% of the right documents don't appear even among the 40 candidates.
   - Score and instruction changes have reached their limit here.
8. **The made-up figures move around between versions (7–11%).** This matches what we saw when we checked Gemma against Claude: Gemma is reliable on whether an answer is right, but much less so on exactly which answers contain made-up claims.



## Limits

- **One reviewer (Gemma) graded everything.** Claude's full review of Benchmark 2 is still to do.
- **Each version was asked each question once.**
- **We looked at the test questions to choose changes.** The variant B results on the test questions led to version D. That makes the test-question figures for C and D slightly optimistic. The tuning-question figures were not affected.
- **This was Handbook only.** Onyx's figures are from Benchmark 2, with the same model and reviewer.



## Next steps

1. **Use version C's settings** where Handbook is deployed, after a check on real company data.
2. **Close the broad-question gap with document-level search.**
   - Find the relevant documents first, using titles or a short summary of each document made once when it's added. That costs AI once per document, not per question.
   - Then read more of each document.
   - For connected sources like Notion, Linear and Slack, also try the knowledge graph, which is already built, for project questions.
3. **Have Claude grade these runs and Benchmark 2,** to confirm the accuracy and made-up figures.

---

The data is in `evaluation/reports/bench2-followup/`:

- answers in `runs/`;
- Gemma's grades in `reviews_v2/`;
- the calibration scores in `calib_pool.jsonl`.

The run script is `evaluation/erb/run_bench2_followup.sh`.
