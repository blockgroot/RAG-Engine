# Benchmark 1: Token use and context management, Handbook vs Onyx

> **Pilot, not the full benchmark.** 200 EnterpriseRAG-Bench questions (50 dev / 150 test, all 10 types) over 1374 documents (374 gold + 1000 noise; the real corpus has ~507,000). Less noise makes search easier, so accuracy here is higher than the full benchmark would show. Tokens are measured exactly, from the provider's own usage field, by one proxy both systems call.

Generated 2026-10-06 · Handbook commit `f6cf3bc` · Onyx `v4.8.4` · answer model `nvidia/nemotron-3-super-120b-a12b` (both systems, through the proxy) · reviewer `gemini-3.1-flash-lite` (one call per answer)

## Main result (test split, 150 questions, run 1)

| | Handbook (core mode) | Onyx v4.8.4 | Basic search, top 10 (reference) |
| --- | --- | --- | --- |
| **LLM tokens per correct answer**¹ | 7,988 | 146,856 | 7,174 |
| LLM tokens per question (mean) | 3,675 | 86,156 | 3,252 |
| LLM tokens per question (median) | 3,231 | 43,075 | 2,920 |
| LLM tokens per question (p90) | 5,329 | 272,821 | 4,557 |
| Answers with a runaway call (≥100k output tokens)⁵ | 0 | 3 | 0 |
| — of which input | 2,656 | 69,681 | 2,243 |
| — of which output (incl. reasoning) | 1,019 | 16,475 | 1,009 |
| Cost per question, if paid² | $0.00070 | $0.01369 | $0.00066 |
| Cost per correct answer, if paid² | $0.00152 | $0.02333 | $0.00145 |
| LLM calls per question | 2.57 | 11.99 | 1.00 |
| Largest single request (input tokens) | 2,479 | 49,470 | 3,028 |
| Documents retrieved (Handbook: sent to the model) | 3.0 | 48.3 | 6.2 |
| Context precision (right docs ÷ docs retrieved) | 0.63 | 0.05 | 0.31 |
| Document recall %, retrieved (anywhere in the search results)⁷ | 80 | 82 | 80 |
| Document recall %, used for the answer⁷ | 80 | 66 | 80 |
| **Win %** (correct + honest "I don't know") | 46 | 59 | 45 |
| Correct % | 35 | 49 | 33 |
| Partial % | 1 | 2 | 11 |
| Honest "I don't know" % | 11 | 10 | 13 |
| **Made up %** (claim not in the context) | 47 | 34 | 33 |
| **Wrong "I don't know" %** | 1 | 5 | 3 |
| Wrong but grounded % | 5 | 1 | 7 |
| Main point correct % (ignoring groundedness) | 59 | 80 | 63 |
| Grounded % (no unsupported claim) | 50 | 63 | 67 |
| Required facts present % | 71 | 83 | 65 |
| Time to first word, median s³ | 9.7 | 41.5 | 7.2 |
| Time to first word, p90 s³ | 23.1 | 267.1 | 20.3 |
| Total time, median s³ | 9.7 | 45.3 | 7.2 |
| Searched the documents %⁴ | n/a | 100 | n/a |
| Answers whose every search crashed (no documents)⁶ | 0 | 9 | 0 |
| Win % excluding those⁶ | 46 | 62 | 45 |
| Failed LLM calls / errored questions / unreviewed | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

¹ All LLM tokens (answer path + background) ÷ wins. ² At the answer model's published price, $0.09 / $0.45 per 1M input / output tokens; the run itself used a free tier. ³ Free-tier rate-limit waits removed. Handbook streams only an already-decided answer, so its first word is the end of the pipeline; Onyx's is the first token of its answer call. ⁵ A model call that kept reasoning until the model's 131,072-token output ceiling without answering. Onyx sends its section-selection call with no output cap; Handbook caps its answer call (8,000 here). Counted in every token figure, as it is real spend. ⁶ Onyx's search tool crashes when Jina's free tier rate-limits its embedding calls (an error-formatting bug in Onyx: `can only concatenate str (not "list") to str`). When every search in an answer crashed, Onyx answered with no documents. These were asked a second time; the ones still blind are counted in every figure as Onyx's answer, and win % without them is shown so the free-tier effect is visible. ⁷ Retrieved: Handbook and basic send everything they retrieve to the model, so both rows match for them; Onyx retrieves ~45–50 documents across all its searches, shows its selection step ~32 sections from ~26 of them, keeps ~2, and cites ~1. Used for the answer = Handbook/basic: the documents sent to the model; Onyx: the documents its answer cited (a lower bound: an answer can use a section without citing it). ⁴ Onyx decides per question whether to search; Handbook and the basic reference always retrieve. The basic reference is plain top-10 vector search plus EnterpriseRAG-Bench's own answer prompt in one call, on Handbook's index: not a product, it shows what each product's extra steps buy.

## Run-to-run variation (50 repeat test questions, 3 runs)

| | Handbook (core mode) | Onyx v4.8.4 | Basic search, top 10 (reference) |
| --- | --- | --- | --- |
| Win %, mean (min–max) | 45 (42–48) | 59 (50–66) | 38 (34–42) |
| Made up % | 47 (46–48) | 33 (26–40) | 39 (38–40) |
| Wrong "I don't know" % | 1 (0–2) | 4 (2–6) | 1 (0–2) |
| Tokens per question | 3,745 (3,702–3,787) | 91,930 (84,779–100,588) | 3,361 (3,353–3,368) |
| Same bucket in all 3 runs % | 68 | 60 | 70 |

## By question type (test split, run 1)

| Type | Win % Handbook (core mode) | Win % Onyx v4.8.4 | Win % Basic search, top 10 (reference) | Tokens/q Handbook (core mode) | Tokens/q Onyx v4.8.4 | Tokens/q Basic search, top 10 (reference) |
| --- | --- | --- | --- | --- | --- | --- |
| basic | 64 | 64 | 50 | 2,949 | 69,071 | 3,036 |
| completeness (breadth) | 7 | 33 | 13 | 3,617 | 75,114 | 3,872 |
| conflicting_info | 40 | 67 | 20 | 3,179 | 42,085 | 2,722 |
| constrained | 47 | 33 | 60 | 3,631 | 83,775 | 3,271 |
| high_level (breadth) | 12 | 50 | 12 | 3,044 | 112,552 | 2,976 |
| info_not_found | 100 | 93 | 100 | 6,371 | 266,910 | 3,248 |
| intra_document_reasoning | 53 | 67 | 53 | 3,819 | 67,106 | 3,915 |
| miscellaneous | 67 | 73 | 73 | 2,772 | 61,613 | 2,377 |
| project_related | 0 | 33 | 7 | 3,732 | 64,744 | 3,273 |
| semantic | 47 | 67 | 47 | 3,676 | 38,877 | 3,807 |

## Where the tokens go (test split, run 1, per question)

**Handbook (core mode)**

| Step | Calls / question | Input / question | Output / question |
| --- | --- | --- | --- |
| handbook:generate | 1.09 | 2,289 | 853 |
| handbook:tone | 1.08 | 251 | 17 |
| handbook:split compound question | 0.31 | 53 | 105 |
| handbook:search recovery | 0.09 | 63 | 44 |

**Onyx v4.8.4**

| Step | Calls / question | Input / question | Output / question |
| --- | --- | --- | --- |
| onyx:section selection | 1.55 | 42,454 | 12,976 |
| onyx:answer (tool choice, then final answer) | 2.98 | 15,507 | 1,339 |
| onyx:section relevance | 4.34 | 8,906 | 1,399 |
| onyx:query rewrite | 2.04 | 1,222 | 454 |
| onyx:time filter | 1.00 | 1,329 | 263 |
| onyx:section selection [background] | 0.01 | 174 | 34 |
| onyx:answer (tool choice, then final answer) [background] | 0.07 | 89 | 9 |

**Basic search, top 10 (reference)**

| Step | Calls / question | Input / question | Output / question |
| --- | --- | --- | --- |
| basic:generate | 1.00 | 2,243 | 1,009 |

## How this was run, and where it departs from benchmarks.md

- Same answer model, same documents, same questions for both systems (rules 1, 2). Nothing was tuned on the test split (rule 3).
- **Rule 4, partly:** 3 runs on 50 test questions (5 per type), 1 run on the rest.
- **Rule 5, partly:** one reviewer call per answer (correctness, facts and groundedness together), not two judges from different companies. `handcheck.csv` holds a 10% sample for a person to check.
- Groundedness is checked against the exact input the answer model received, captured by the proxy.
- Scores use our own review prompt, so they are not comparable with the public EnterpriseRAG-Bench leaderboard. The Handbook-vs-Onyx comparison is fair: both get the identical review.
- Handbook ran at its product defaults except: `RAG_MAX_ANSWER_TOKENS=8000` (default 700; the answer model reasons first, and at 2000 it was cut off mid-sentence; Onyx sets no such cap); NO per-chunk AI context line at ingest (default on; Onyx's equivalent, contextual RAG, is also off by default, so neither system has it); knowledge graph, live tools, personal memory, web search and the injection guard off (they need real connectors or users, or would let either system answer from the web). The answer cache is on, as shipped; with 200 distinct questions and a 5-minute lifetime it never hits. Onyx ran as shipped, limited to internal search, deep research off.
- Every raw answer, proxy call and review is kept in this folder (rule 6).

## Things that happened during the run

- **Onyx's search crashes when Jina rate-limits it.** Onyx sends several embedding requests at once; when Jina's free tier answers 429 and Onyx's retries run out, an error-formatting bug in Onyx (`can only concatenate str (not "list") to str`) crashes the search tool. The agent usually searches again and recovers. When every search failed, Onyx answered with no documents ("blind", 17 answers in the first pass, mostly in one burst around 00:00–00:15 IST). Those answers were set aside (`runs/onyx.requeued.jsonl`) and asked again. 16 of the 21 were still blind on the second try (the same questions trigger the burst again). They are counted as Onyx's answer, and the report also shows win % without them: the crash is a real Onyx defect, but the rate limit that triggers it is our free tier.
- **Onyx's user memory was not empty.** Onyx stores short notes about the user. Five were saved from the first setup questions, before the benchmark started, when Onyx's only tool was `add_memory`. They describe question topics, never answers, and Onyx includes them in most of its prompts (about 150 extra tokens per call). They were left in place for the whole run, so every Onyx answer had the same five. Handbook's personal memory was off.
- **NVIDIA's free tier sometimes accepted a call and never answered.** Before the proxy retried such calls (from 21:36 IST), three answers included a hang or a manual pause in their time (Handbook qst_0420 and qst_0491, basic qst_0324). They were set aside and asked again.
- **Onyx's hard questions ran past nginx's 300 s limit** in Onyx's Docker setup, which cut two answers off. The limit was raised to 1,800 s and the two questions were asked again. On hard questions Onyx's agent searched up to 5 times: up to 32 calls, about 283k input tokens and 12 minutes for one answer.
- **Some Onyx answers did not search at all** when the question itself held everything needed (e.g. qst_0314, a date calculation), which is Onyx's agent deciding, not a fault.
- **Runaway reasoning on Onyx's section-selection call.** On 10 calls (9 Onyx answers of the first 274) the answer model kept reasoning until its 131,072-token output ceiling and returned no text; each took 12–18 minutes. Onyx sends that call with no output cap, so it cannot stop it; Handbook caps its answer call (8,000 tokens here). These are real tokens and real time, so they stay in every figure: about 6.6% of Onyx's tokens. The report shows the median and p90 beside the mean so they cannot hide in an average, and counts the answers affected.

## Second-reviewer check (rule 5), and what it says about "made up"

A 10% sample (45 answers, 15 per system, in `handcheck.csv`) was reviewed a second time by a reviewer from a different company (Claude, Anthropic), against the same evidence: the question, gold answer, required facts, the answer, and the exact context the answer model saw. This is a second AI judge, not the human check; the `person_*` columns are left for a person.

| | Handbook | Onyx | Basic | All |
| --- | --- | --- | --- | --- |
| Reviewers agree on the bucket | 10 / 15 | 6 / 15 | 7 / 15 | **23 / 45 (51%)** |
| "Made up", first reviewer (Gemini) | 7 | 11 | 5 | 23 |
| "Made up", second reviewer (Claude) | 2 | 4 | 0 | 6 |
| Wins (correct + honest "I don't know"), Gemini / Claude | 7 / 8 | 4 / 5 | 5 / 5 | 16 / 18 |

**Wins and correctness hold up; the made-up rate does not.** The two reviewers broadly agree on which answers win, but only 5 of Gemini's 23 "made up" verdicts were confirmed. In most of the others, the flagged claim is in the context word for word, or the answer took a real fact from the wrong document. That is a wrong answer, not an invented one. So the **made-up % in the main table is inflated, for every system**, by roughly 3 to 4 times in this sample, and should not be quoted as is. Per benchmarks.md rule 5 ("if the judges disagree often, the grading rules are unclear: fix the rules, not the numbers"), the review prompt needs two fixes before the made-up figures are trusted:
1. Make the reviewer quote the context passage for any claim it calls unsupported.
2. Separate "invented" from "taken from the wrong document" (which is "wrong").

Then all 900 answers need to be reviewed again.
