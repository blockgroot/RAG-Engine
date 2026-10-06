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
