# Benchmark 1: Token use and context management, Handbook vs Onyx

> **Pilot, not the full benchmark.** 200 EnterpriseRAG-Bench questions (50 dev / 150 test, all 10 types) over 1374 documents (374 gold + 1000 noise; the real corpus has ~507,000). Less noise makes search easier, so accuracy here is higher than the full benchmark would show. Tokens are measured exactly, from the provider's own usage field, by one proxy both systems call.

Generated 2026-10-06 · Handbook commit `2b0673f` · Onyx `v4.8.4` · answer model `nvidia/nemotron-3-super-120b-a12b` (both systems, through the proxy) · reviewers: Claude (Anthropic) for every test answer of run 1; Gemini 3.1 Flash-Lite for runs 2-3

## Main result (test split, 150 questions, run 1)

| | Handbook (core mode) | Onyx v4.8.4 | Basic search, top 10 (reference) |
| --- | --- | --- | --- |
| **LLM tokens per correct answer**¹ | 7,763 | 120,217 | 7,998 |
| LLM tokens per question (mean) | 3,675 | 86,156 | 3,252 |
| LLM tokens per question (median) | 3,231 | 43,075 | 2,920 |
| LLM tokens per question (p90) | 5,329 | 272,821 | 4,557 |
| Answers with a runaway call (≥100k output tokens)⁵ | 0 | 3 | 0 |
| — of which input | 2,656 | 69,681 | 2,243 |
| — of which output (incl. reasoning) | 1,019 | 16,475 | 1,009 |
| Cost per question, if paid² | $0.00070 | $0.01369 | $0.00066 |
| Cost per correct answer, if paid² | $0.00147 | $0.01910 | $0.00161 |
| LLM calls per question | 2.57 | 11.99 | 1.00 |
| Largest single request (input tokens) | 2,479 | 49,470 | 3,028 |
| Documents retrieved (Handbook: sent to the model) | 3.0 | 48.3 | 6.2 |
| Context precision (right docs ÷ docs retrieved) | 0.63 | 0.05 | 0.31 |
| Document recall %, retrieved (anywhere in the search results)⁷ | 80 | 82 | 80 |
| Document recall %, used for the answer⁷ | 80 | 66 | 80 |
| **Win %** (correct + honest "I don't know") | 47 | 72 | 41 |
| Correct % | 39 | 72 | 32 |
| Partial % | 20 | 7 | 21 |
| Honest "I don't know" % | 9 | 0 | 9 |
| **Made up %** (claim not in the context) | 7 | 5 | 10 |
| **Wrong "I don't know" %** | 3 | 3 | 7 |
| Wrong but grounded % | 23 | 13 | 22 |
| Main point correct % (ignoring groundedness) | 69 | 80 | 65 |
| Grounded % (no unsupported claim) | 93 | 95 | 90 |
| Required facts present % | 68 | 83 | 62 |
| Time to first word, median s³ | 9.7 | 41.5 | 7.2 |
| Time to first word, p90 s³ | 23.1 | 267.1 | 20.3 |
| Total time, median s³ | 9.7 | 45.3 | 7.2 |
| Searched the documents %⁴ | n/a | 100 | n/a |
| Answers whose every search crashed (no documents)⁶ | 0 | 9 | 0 |
| Win % excluding those⁶ | 47 | 77 | 41 |
| Failed LLM calls / errored questions / unreviewed | 0 / 0 / 0 | 0 / 0 / 90 | 0 / 0 / 0 |

¹ All LLM tokens (answer path + background) ÷ wins. ² At the answer model's published price, $0.09 / $0.45 per 1M input / output tokens; the run itself used a free tier. ³ Free-tier rate-limit waits removed. Handbook streams only an already-decided answer, so its first word is the end of the pipeline; Onyx's is the first token of its answer call. ⁵ A model call that kept reasoning until the model's 131,072-token output ceiling without answering. Onyx sends its section-selection call with no output cap; Handbook caps its answer call (8,000 here). Counted in every token figure, as it is real spend. ⁶ Onyx's search tool crashes when Jina's free tier rate-limits its embedding calls (an error-formatting bug in Onyx: `can only concatenate str (not "list") to str`). When every search in an answer crashed, Onyx answered with no documents. These were asked a second time; the ones still blind are counted in every figure as Onyx's answer, and win % without them is shown so the free-tier effect is visible. ⁷ Retrieved: Handbook and basic send everything they retrieve to the model, so both rows match for them; Onyx retrieves ~45–50 documents across all its searches, shows its selection step ~32 sections from ~26 of them, keeps ~2, and cites ~1. Used for the answer = Handbook/basic: the documents sent to the model; Onyx: the documents its answer cited (a lower bound: an answer can use a section without citing it). ⁴ Onyx decides per question whether to search; Handbook and the basic reference always retrieve. The basic reference is plain top-10 vector search plus EnterpriseRAG-Bench's own answer prompt in one call, on Handbook's index: not a product, it shows what each product's extra steps buy.

## Same questions, every system graded (60 test questions, run 1)

The main table grades every Handbook and basic answer but only a sample of Onyx's, and that sample has none of the hardest types. Compare quality here. Types: basic 22, semantic 15, intra_document_reasoning 8, conflicting_info 8, constrained 7.

| | Handbook (core mode) | Onyx v4.8.4 | Basic search, top 10 (reference) |
| --- | --- | --- | --- |
| **LLM tokens per correct answer**¹ | 6,261 | 97,199 | 8,033 |
| LLM tokens per question (mean) | 3,339 | 69,659 | 3,347 |
| LLM tokens per question (median) | 3,057 | 40,614 | 2,943 |
| **Win %** (correct + honest "I don't know") | 53 | 72 | 42 |
| Correct % | 53 | 72 | 42 |
| Partial % | 18 | 7 | 25 |
| **Made up %** (claim not in the context) | 2 | 5 | 3 |
| **Wrong "I don't know" %** | 7 | 3 | 10 |
| Wrong but grounded % | 20 | 13 | 20 |
| Main point correct % (ignoring groundedness) | 73 | 80 | 70 |
| Grounded % (no unsupported claim) | 98 | 95 | 97 |
| Required facts present % | 75 | 83 | 63 |
| Time to first word, median s³ | 7.7 | 33.3 | 6.3 |

## Run-to-run variation (50 repeat test questions, 3 runs)

| | Handbook (core mode) | Onyx v4.8.4 | Basic search, top 10 (reference) |
| --- | --- | --- | --- |
| Win %, mean (min–max) | 41 (38–44) | 62 (50–75) | 36 (34–38) |
| Made up % | 34 (8–48) | 26 (5–40) | 29 (8–40) |
| Wrong "I don't know" % | 2 (0–4) | 5 (4–6) | 1 (0–2) |
| Tokens per question | 3,745 (3,702–3,787) | 91,930 (84,779–100,588) | 3,361 (3,353–3,368) |
| Same bucket in all 3 runs % | 34 | 50 | 42 |

Run 1 is graded by Claude; runs 2-3 by Gemini 3.1 Flash-Lite (v1), whose made-up calls proved unreliable (see notes). Read the made-up row and the same-bucket row here as rough; the token row is exact.

## By question type (test split, run 1)

| Type | Win % Handbook (core mode) | Win % Onyx v4.8.4 | Win % Basic search, top 10 (reference) | Tokens/q Handbook (core mode) | Tokens/q Onyx v4.8.4 | Tokens/q Basic search, top 10 (reference) |
| --- | --- | --- | --- | --- | --- | --- |
| basic | 68 | 77 | 41 | 2,949 | 69,071 | 3,036 |
| completeness (breadth) | 27 | n/a | 13 | 3,617 | 75,114 | 3,872 |
| conflicting_info | 53 | 75 | 47 | 3,179 | 42,085 | 2,722 |
| constrained | 47 | 43 | 53 | 3,631 | 83,775 | 3,271 |
| high_level (breadth) | 0 | n/a | 0 | 3,044 | 112,552 | 2,976 |
| info_not_found | 87 | n/a | 87 | 6,371 | 266,910 | 3,248 |
| intra_document_reasoning | 47 | 62 | 53 | 3,819 | 67,106 | 3,915 |
| miscellaneous | 73 | n/a | 67 | 2,772 | 61,613 | 2,377 |
| project_related | 0 | n/a | 0 | 3,732 | 64,744 | 3,273 |
| semantic | 40 | 80 | 27 | 3,676 | 38,877 | 3,807 |

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

## Final groundedness review (Claude, run 1)

The v2 prompt (quoted evidence, wrong-source separated from invented) was tried on Gemini and still agreed with Claude on only 1 of 6 made-up calls, so run 1 was regraded by Claude instead, with the same rules: every specific claim searched for in the exact context the answer model saw; only a claim found nowhere in it counts as made up; a real fact attached to the wrong thing makes the answer partial or wrong, never made up. Run 1 quality in `REPORT.md` comes from these reviews only (`reviews_claude/`); runs 2-3 stay on Gemini v1.

- **Coverage:** every Handbook and basic test answer (150 each); 60 of Onyx's 150. The session's usage limit cut Onyx's batches short. The 60 are only five types (basic 22, semantic 15, conflicting_info 8, intra_document_reasoning 8, constrained 7) and none of the hardest ones. So Onyx's quality figures in the main table are **not** comparable with the other two; the report's "Same questions" table compares all three on those 60. With 60 answers, a win % has a margin of about ±12 points (95%).
- **Gemini over-called made up by about 5 times.** On the 360 answers both graded: Gemini flagged 143, Claude 28, and 25 of Claude's 28 were also Gemini's. The two agree on "main point correct" for 313 of 360 (87%), so the win figures were broadly right; only the made-up figures were not.

| Same 60 questions | Handbook | Onyx | Basic |
| --- | --- | --- | --- |
| Win % | 53 | 72 | 42 |
| Made up % | 2 | 5 | 3 |
| Grounded % | 98 | 95 | 97 |
| Main point correct % | 73 | 80 | 70 |
| Tokens per correct answer | 6,261 | 97,199 | 8,033 |

**Verdict:** all three systems are well grounded; invention is rare (2–5% on the shared questions; 7% Handbook and 10% basic over all 150). The gap between the systems is accuracy, not invention: Onyx answers more questions right (about 19 points above Handbook on the shared 60), and it uses about 15 times the tokens per correct answer.

## Follow-up: does Handbook get better if it reads more? (dev split, 50 questions, one run)

Benchmark 1 suggested the Handbook-Onyx accuracy gap came from how much text the model is given (5 short passages, 6,000 characters) rather than search. To test this, Handbook was run on the 50 dev (tuning) questions with more passages: `--set RAG_TOP_K=10 RETRIEVAL_CANDIDATE_POOL=20 RAG_MAX_CONTEXT_CHARS=20000` and `RAG_TOP_K=20 ... RAG_MAX_CONTEXT_CHARS=40000`. Same model, embeddings, reranker and proxy; the answer cache was cleared before each run. All three settings were graded by Claude with the same instructions as the test split (`reviews_claude_dev/`).

**First attempt was invalid and was deleted.** `load_handbook.py` reloaded `.env.bench` with override when it was imported, after the runner had applied `--set`. That put `RAG_MAX_CONTEXT_CHARS` back to 6,000 and the pool back to 16, so both variants sent the model the same ~7 passages. `context_chars` in the log counts what was retrieved, not what fit the prompt, which hid it. Fixed (the reload now happens only in the loader's own `main()`), and every Handbook row now records the settings in effect (`raw.settings`).

| | Default (5) | 10 passages | 20 passages |
| --- | --- | --- | --- |
| Tokens per question (median) | 3,407 | 5,133 | 7,419 |
| Tokens per correct answer | 8,561 | 12,039 | 17,613 |
| Documents sent to the model | 2.9 | 5.4 | 12.1 |
| Document recall % | 86 | 89 | 92 |
| Win % | 44 | 50 | 46 |
| Made up % | 8 | 4 | 8 |
| Main point correct % | 76 | 80 | 78 |
| Required facts present % | 69 | 74 | 71 |
| Time to first word, median s | 13.2 | 15.4 | 18.6 |

**Result: reading more did not clearly help.** Win % moved 44 → 50 → 46. On 50 questions that is 3 questions either way, inside the noise (±14 points). From default to 20 passages, 4 questions became wins and 3 stopped being wins. "List everything" (completeness) questions stayed at 0 of 5 in every setting, and project questions at 0–1 of 5, though those were the types the hypothesis was about. Tokens per correct answer doubled. So passage count alone is not what separates Handbook from Onyx. More likely candidates are Onyx's question rewriting and repeated searches, or the model's handling of long multi-part answers. Ten passages is the only setting with a hint of gain (+3 questions, fewer made-up claims) and would need the 150 test questions to confirm.
