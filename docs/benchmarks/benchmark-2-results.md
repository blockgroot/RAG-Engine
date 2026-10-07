# Handbook vs Onyx, second run: a different AI model

*Benchmark 2 (accuracy re-check with a different model) · 7 October 2026*

Benchmark 1's accuracy figures were not convincing on their own, so we ran the same test again with a different AI model, to see whether the results hold. Everything else was kept the same where possible: the same 150 test questions, the same 1,374 documents and the same unmodified Onyx (v4.8.4). Handbook ran with the four accuracy changes that came out of Benchmark 1.

## In short

- **With this model, Handbook and Onyx are equally accurate.** Each got the key answer right on about 65% of the 150 questions (Handbook 65.3%, Onyx 64.7%). In Benchmark 1 Onyx led by 7 points on the same measure.
- **Handbook is still far cheaper:** about **16× fewer AI tokens** per question (4.2k vs 69k) and **about 2× faster** to the first word (6 s vs 11 s).
- **Onyx never says "I don't know".** It said so in only 5 of 150 answers. On the 15 questions whose answer is not in the documents, it answered 14 anyway, inventing details such as a blockchain contract address. Handbook correctly said "I don't know" to all 15.
- **Onyx made things up much more often with this model:** 47% of its answers had at least one invented claim, against 11% for Handbook. The second reviewer (Claude) saw the same pattern on a sample.
- **Onyx is still ahead on broad questions** (whole projects, big-picture summaries), and Handbook is ahead on narrow ones with conditions attached.



## What changed from Benchmark 1

|                      | Benchmark 1                                    | Benchmark 2                                                     |
| -------------------- | ---------------------------------------------- | --------------------------------------------------------------- |
| AI model (answers)   | NVIDIA Nemotron 3 Super                        | **OpenAI gpt-oss-120b** (open model), "low" reasoning effort, on Ollama Cloud |
| Reviewer (grading)   | Anthropic Claude (Gemini first, set aside)     | **Google Gemma 4 31B**, checked against Claude on 45 answers    |
| Questions            | 150 test questions; 50 asked three times       | The same 150 test questions, **asked once**                     |
| Handbook settings    | Default                                        | **The four accuracy changes switched on** (three; see below)    |
| Onyx                 | v4.8.4, unmodified                             | The same                                                        |
| Documents, search    | 1,374 documents, Jina embeddings               | The same                                                        |

**Handbook's settings for this run:**

- **Reads the text around its best matches** (`RAG_NEIGHBOR_CHUNKS=1`).
- **Keeps each fact with its own document** (`RAG_FOCUS_RULE=true`).
- **Searches with two other wordings from the start** (`RECOVERY_PROACTIVE=true`).
- **The weak-result cutoff stayed off.** Its scores were recorded for every answer, so it can be tuned from real numbers later.

**Fairness:**

- All three systems used the same model with the same reasoning setting.
- Every AI call went through one logging proxy, so tokens were counted the same way.
- The reviewer comes from a different company (Google) than the answer model (OpenAI), so no model graded its own work.



## Results at a glance

**Cost and speed (all 150 questions)**

|                                       | Handbook   | Onyx       | Basic pipeline |
| ------------------------------------- | ---------- | ---------- | -------------- |
| AI tokens per question (average)      | **4,215**  | 69,466     | 2,374          |
| AI tokens per question (typical)      | **3,582**  | 37,899     | 2,325          |
| AI tokens, most expensive 10%         | **7,069+** | 191,365+   | 2,870+         |
| AI calls per question (average)       | **3.6**    | 12.1       | 1.0            |
| Time to first word (typical)          | **6.2 s**  | 11.2 s     | 2.4 s          |
| Time to first word, slowest 10%       | **10 s**   | 35 s       | 4.5 s          |
| Found the right documents             | **83%**    | 78%        | 80%            |

**Quality (all 150 questions, graded by Gemma)**

|                                          | Handbook  | Onyx      | Basic pipeline |
| ---------------------------------------- | --------- | --------- | -------------- |
| **Got the key answer right**             | **65.3%** | 64.7%     | 61.3%          |
| Required facts included                  | 69.7%     | **70.5%** | 65.0%          |
| Wins (correct + honest "I don't know")   | **43.3%** | 34.7%     | 40.0%          |
| Fully correct (every fact, nothing made up) | 33.3%  | **34.0%** | 30.0%          |
| Correctly said "I don't know"            | **10.0%** | 0.7%      | 10.0%          |
| Wrongly said "I don't know"              | 9.3%      | **2.7%**  | 8.0%           |
| At least one made-up claim               | **10.7%** | 47.3%     | 18.7%          |

- **"Got the key answer right" is the figure to trust most.** The two reviewers agreed on it 89% of the time (see "Checking the reviewer").
- **The made-up row shows the direction, not exact numbers.** The two reviewers agreed on which system invents most, but often not on which individual answers.
- "Typical" means the median, the middle question.

**The same 60 questions as Benchmark 1** (the ones graded for Onyx there)

|                           | Handbook | Onyx    | Basic pipeline |
| ------------------------- | -------- | ------- | -------------- |
| Got the key answer right  | 72%      | **77%** | 68%            |
| AI tokens per question    | **4,089**| 50,436  | 2,362          |

On these easier questions Onyx is still slightly ahead, by 5 points instead of Benchmark 1's 7. The full 150 includes the hard types that Benchmark 1 could not grade for Onyx, and there the two even out.



## Onyx never says "I don't know"

This is the clearest finding of this run.

- **Onyx said "I don't know" in 5 of its 150 answers.** Handbook said it in 29, and the basic pipeline in 27.
- **The benchmark has 15 questions whose answer is deliberately missing from the documents.** The right reply is to say so.

| Questions with no answer in the documents | Handbook | Onyx | Basic pipeline |
| ----------------------------------------- | -------- | ---- | -------------- |
| Said "I don't know" (correct)             | **15**   | 1    | **15**         |
| Gave an answer anyway                     | 0        | 14   | 0              |

**All 14 of Onyx's answers there were graded as containing made-up claims.** Two examples:

- **Asked which blockchain network and smart-contract address a feature uses** (the documents say neither), Onyx answered with a formatted table: "Ethereum Mainnet" and a contract address starting `0xA1B2c3D4e5F6…`. Both are invented. Handbook replied: "I don't have anything about that in this workspace's connected content."
- **Asked for the exact format of a security token** that no document describes, Onyx produced a full table of fields, types and encodings, all invented.

**Why it happens:**

- **Onyx's answer prompt asks the model to be helpful,** and gpt-oss fills the gaps from its general knowledge.
- **Handbook has two guards against this.**
  - A confidence check: if nothing matches well enough, it says "I don't know" without calling the AI at all.
  - A strict instruction to answer only from the passages it was given.

  Those guards are what make the difference here.

**The cost for Handbook:** it is more cautious, and sometimes too cautious. It wrongly said "I don't know" on 9% of questions whose answer was in the documents, against Onyx's 3%.



## Answer quality by question type

Key answer right, all 150 questions:

| What the question asks                                          | Questions | Handbook | Onyx   | Basic pipeline |
| --------------------------------------------------------------- | --------- | -------- | ------ | -------------- |
| One simple fact                                                 | 22        | 17       | **19** | 15             |
| A fact, asked in different words than the documents use         | 15        | 11       | **12** | 9              |
| Documents disagree, so it must pick the current or correct one  | 15        | 11       | **14** | 11             |
| Several facts from one long document                            | 15        | 11       | **12** | 10             |
| Narrow questions with conditions ("in a Private deployment…")   | 15        | **10**   | 6      | 9              |
| "List everything" about a topic                                 | 15        | 5        | **7**  | 4              |
| A whole project or customer situation                           | 15        | 2        | **10** | 3              |
| A big-picture summary                                           | 8         | 1        | **4**  | 1              |
| Mixed, other                                                    | 15        | **15**   | 12     | **15**         |
| The answer is not in the documents                              | 15        | **15**   | 1      | **15**         |

**Onyx's lead is on the broad questions:** whole projects (10 vs 2), big-picture summaries (4 vs 1) and "list everything" (7 vs 5).

- These need many documents, and Onyx's repeated searching and section-picking gather them.
- Handbook reads 5 passages, which is not enough for them.

This is the gap to work on next.

**Handbook's lead is on questions with no answer, narrow questions with conditions, and mixed questions.** On these, staying close to the passages pays off.



## Token usage

**AI tokens per question (average)**

```
Handbook        ██                                          4,215
Onyx            ████████████████████████████████████████   69,466
Basic pipeline  █                                           2,374
```

- **Onyx's token use swings widely.** Its most expensive answer used **409,561 tokens across 64 AI calls**; Handbook's most expensive used 10,452 tokens in 7 calls.
- **Handbook uses more tokens than in Benchmark 1** (4.2k vs 3.7k on average), because of two of the new settings.
  - The up-front rewording adds a small AI call to every question.
  - The neighbouring text makes each answer read a little more.

  It is still 16× below Onyx.
- **No runaway "thinking" this time.** gpt-oss at low reasoning effort thinks for only about 15–30 tokens a call. In Benchmark 1, three Onyx calls hit the model's 131,000-token limit without answering.



## Checking the reviewer

Benchmark 1 taught us not to trust one AI reviewer blindly: Gemini called 5× too many answers "made up". So before grading all 450 answers with Gemma, we had Claude grade the same 45 answers (15 per system, the same sample used in Benchmark 1) and compared.

|                                         | Handbook | Onyx  | Basic | All      |
| --------------------------------------- | -------- | ----- | ----- | -------- |
| Reviewers agree on "key answer right"   | 14/15    | 13/15 | 13/15 | **40/45 (89%)** |
| Key answer right, Claude                | 12       | 10    | 9     | 31       |
| Key answer right, Gemma                 | 11       | 8     | 7     | 26       |
| Answers with a made-up claim, Claude    | 1        | 6     | 2     | 9        |

- **Gemma is reliable for "is the answer right?"**
  - All 5 disagreements were Gemma being stricter than Claude.
  - 3 of them were honest "I don't know" answers that Gemma marked wrong.
  - So Gemma's accuracy figures, if anything, slightly undercount every system.
- **Gemma is less reliable for "what exactly was made up".**
  - It flagged about as many answers as Claude (8 vs 9), but only 3 were the same answers.
  - Both reviewers agree that Onyx invents most: Claude flagged 6 of 15 Onyx answers, 1 of 15 Handbook answers and 2 of 15 basic answers.
  - The exact percentages in the made-up row should wait for Claude's full review (next steps).
- **A few of Gemma's replies came back in the wrong format.** In 16 of about 2,600 per-fact verdicts it wrote `"true"` as text, or wrapped the value in an object. These were read as plain yes/no when the figures were calculated.



## What we ran into

1. **Finding a free model took several tries.** We have no paid account, so only free services were possible.
   - **Mistral:** needs credit added before its API works.
   - **NVIDIA:** its free service would not serve the non-NVIDIA models we tried; they failed or timed out.
   - **Gemini:** the free tier allows 20 requests a day per project, and its newer models cannot switch "thinking" off.
   - **Ollama Cloud worked.** Its free plan serves gpt-oss-120b and Gemma 4 with a monthly allowance. This whole run used a small part of it, spread over two accounts.
2. **gpt-oss cannot switch thinking off completely.** At "low" effort it thinks for 15–30 tokens a call, and those tokens are counted in the figures above.
3. **The Jina search key ran out of credit mid-run.**
   - Handbook stopped itself after 3 failed questions. Onyx kept going and answered some questions with no documents.
   - We switched both products to a backup Jina key and resumed.
   - The 3 failed Handbook questions were asked again and answered normally.
4. **Onyx's searches hit Jina's per-minute limit.** Onyx sends 5–8 searches at once for each question, and Jina's free tier sometimes rejects part of a burst. Onyx retries these, so most answers were not affected.
5. **Eight Onyx answers had no documents, because of Onyx's own date filter.**
   - **What happened:** 11 Onyx answers came back with no documents. We asked them again, twice, the second time with a minute between questions, and 3 then found their documents. The other 8 kept coming back empty.
   - **The cause:** Onyx's log shows that for each of the 8, its model turned a date in the question ("the March 2026 incident", "the 2026-01-15 game day", "H1 2025") into a filter that searched only documents updated in that period. Our test documents carry different dates, so nothing matched.
   - **Why they count:** this is Onyx's own behaviour with this model, not an outside failure. Benchmark 1's model did not do it.
   - **What they did:** in 4 of the 8 Onyx said it had nothing; in the other 4 it answered anyway and was graded as making things up.
   - **Without them:** Onyx gets the key answer right on 68.3% of the remaining 142 questions.
6. **Running in parallel.** To finish in one day, Onyx ran on one Ollama account while Handbook and then the basic pipeline ran on the other, each through its own logging proxy, so token counts were never mixed. All 450 answers finished with no errors.



## Observations

1. **The accuracy gap from Benchmark 1 did not hold with a different model.**
   - On all 150 questions, Handbook and Onyx tie on getting the key answer right (65% each).
   - On Benchmark 1's 60 questions, Onyx's lead shrank from 7 points to 5.
   - **Accuracy depends on the model at least as much as on the product.**
2. **Handbook is still 16× cheaper and about 2× faster,** with predictable cost from question to question.
3. **Onyx answers everything, including what it doesn't know.**
   - It answered 14 of the 15 unanswerable questions with invented details.
   - Overall it made something up far more often than Handbook.
   - For a company knowledge tool, a confident wrong answer is worse than "I don't know".
4. **Handbook's guards against making things up work:** the confidence check and the strict answer prompt. It made up claims in 11% of answers against Onyx's 47%, and was perfect on the unanswerable questions.
5. **Handbook is sometimes too cautious.** It wrongly said "I don't know" on 9% of questions. That is the price of the same guards, and a place to tune.
6. **Onyx's real advantage is broad questions** (whole projects, big-picture, "list everything"), where it gathers many documents. This is Handbook's main gap.
7. **We can't yet say how much Handbook's four new settings helped on their own.**
   - Handbook's key-answer score is about the same as in Benchmark 1 (72% vs 73% on the same 60 questions).
   - But the model and the reviewer both changed, and Gemma grades more strictly than Claude.
   - Measuring the settings needs a run with them on and off, with the same model and reviewer.



## Limits of this run

- **It's still a pilot:** 1,374 documents, not the full 500,000.
- **Each question was asked once,** so there is no check on consistency between runs. With 150 questions a percentage can be off by about ±8 points, so a 1-point gap is a tie.
- **One reviewer graded everything (Gemma).** Claude checked a 45-answer sample. The made-up figures in particular should be confirmed by a full Claude review.
- **Free services only.** Times include free-tier queuing, and Jina's limits forced retries.
- **Different model and reviewer from Benchmark 1,** so the two benchmarks' figures should be compared with care.



## Recommended next steps

1. **Have Claude grade all 450 answers for made-up claims,** the same way as in Benchmark 1, to firm up the groundedness figures.
2. **Improve Handbook on broad questions.** These are the whole-project, big-picture and "list everything" types. Options to test:
   - gathering more documents only for broad questions;
   - searching again when the first pass looks thin;
   - grouping passages by project.
3. **Tune the weak-result cutoff** from the scores recorded in this run (`raw.rerank_scores` in Handbook's records).
4. **Measure each new Handbook setting on its own,** with the same model and reviewer, to see which ones actually help.
5. **Look at Handbook's wrong "I don't know" answers** (9%), to see whether the confidence check or the prompt is too strict.

---

The raw data is in `evaluation/reports/bench2/`:

- answers in `runs/`;
- Gemma's grades in `reviews_v2/`;
- Claude's 45-answer check in `reviews_claude/`.

The run script is `evaluation/erb/run_bench2.sh`.
