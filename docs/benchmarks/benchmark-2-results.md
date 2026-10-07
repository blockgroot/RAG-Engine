# Handbook vs Onyx, second run: a different AI model

*Benchmark 2 (accuracy re-check with a different model) · 7 October 2026*

Benchmark 1's accuracy figures were not convincing on their own. So we ran the same test again with a different AI model, to see whether the results hold.

We kept everything else as close as possible:

- the same 150 test questions;
- the same 1,374 documents;
- the same unmodified Onyx (v4.8.4).

Handbook ran with the accuracy changes that came out of Benchmark 1.

## In short

- **With this model, Handbook and Onyx are equally accurate.** Each got the key answer right on about 65% of the 150 questions (Handbook 65.3%, Onyx 64.7%). In Benchmark 1, Onyx led by 7 points on the same measure.
- **Handbook is still far cheaper and faster:** about **16× fewer AI tokens** per question (4.2k vs 69k), and **about 2× faster** to the first word (6 s vs 11 s).
- **Onyx almost never says "I don't know".** It did so in only 5 of 150 answers.
  - On the 15 questions whose answer is not in the documents, Onyx answered 14 anyway, with invented details such as a blockchain contract address.
  - Handbook correctly said "I don't know" to all 15.
- **Onyx made things up far more often with this model:** 47% of its answers contained at least one invented claim, against 11% for Handbook. A second reviewer (Claude) saw the same pattern on a sample.
- **Onyx is still clearly better on broad questions.** These are questions about a whole project, a big-picture summary, or "list everything". This is where Handbook lags most.
- **Handbook is better when the answer is narrow or missing.** That covers questions with no answer in the documents, questions with conditions attached, and mixed questions.



## What we tested

- **Questions:**
  - The same 150 test questions from *EnterpriseRAG-Bench* as Benchmark 1, covering all 10 question types.
  - This time each was asked **once**; Benchmark 1 asked 50 of them three times.
- **Documents:** the same 1,374 made-up company documents (Slack, email, tickets, docs), with the right documents for every question plus 1,000 unrelated ones as noise.
- **Systems:** Handbook, Onyx v4.8.4 (unmodified), and the same basic pipeline used as a yardstick (plain search, top 10 passages, one AI call).

**What changed from Benchmark 1**

|                    | Benchmark 1                                | Benchmark 2                                                                   |
| ------------------ | ------------------------------------------ | ----------------------------------------------------------------------------- |
| AI model (answers) | NVIDIA Nemotron 3 Super                    | **OpenAI gpt-oss-120b** (open model), "low" reasoning effort, on Ollama Cloud |
| Reviewer (grading) | Anthropic Claude (Gemini first, set aside) | **Google Gemma 4 31B**, checked against Claude on 45 answers                  |
| Repeats            | 50 questions asked three times             | Each question asked once                                                      |
| Handbook settings  | Default                                    | Three of the four new accuracy settings on                                    |

**Handbook's new settings in this run**

- **Reads the text around its best matches:** the paragraph before and after its two best passages.
- **Keeps each fact with its own document:** an extra rule in the answer instructions.
- **Searches with two other wordings from the start,** not only after a search fails.
- **The weak-result cutoff stayed off.** Its scores were recorded for every answer, so it can be tuned later from real numbers.

**Fairness**

- **Same model for everyone:** all three systems used gpt-oss-120b with the same reasoning setting.
- **Same counting:** every AI call went through one logging proxy, so tokens were counted the same way for each system.
- **No self-grading:** the reviewer (Google's Gemma) comes from a different company than the answer model (OpenAI), so no model graded its own work.

**Grading**

- **Gemma graded all 450 answers.** For each one it checked the answer against the correct answer and the exact text the system had been shown. It listed every specific claim, with a quote showing where in that text it came from.
- **Claude graded 45 of the same answers first** (15 per system), to check Gemma could be trusted (see "Checking the reviewer").



## Results at a glance

**Cost and speed (all 150 questions)**

|                                  | Handbook   | Onyx     | Basic pipeline |
| -------------------------------- | ---------- | -------- | -------------- |
| AI tokens per question (average) | **4,215**  | 69,466   | 2,374          |
| AI tokens per question (typical) | **3,582**  | 37,899   | 2,325          |
| AI tokens, most expensive 10%    | **7,069+** | 191,365+ | 2,870+         |
| AI calls per question (average)  | **3.6**    | 12.1     | 1.0            |
| Time to first word (typical)     | **6.2 s**  | 11.2 s   | 2.4 s          |
| Found the right documents        | **83%**    | 78%      | 80%            |

**Quality (all 150 questions, graded by Gemma)**

|                                             | Handbook  | Onyx      | Basic pipeline |
| ------------------------------------------- | --------- | --------- | -------------- |
| **Got the key answer right**                | **65.3%** | 64.7%     | 61.3%          |
| Required facts included                     | 69.7%     | **70.5%** | 65.0%          |
| Wins (correct + honest "I don't know")      | **43.3%** | 34.7%     | 40.0%          |
| Fully correct (every fact, nothing made up) | 33.3%     | **34.0%** | 30.0%          |
| Correctly said "I don't know"               | **10.0%** | 0.7%      | 10.0%          |
| Wrongly said "I don't know"                 | 9.3%      | **2.7%**  | 8.0%           |
| At least one made-up claim                  | **10.7%** | 47.3%     | 18.7%          |

**The same 60 questions as Benchmark 1** (the ones Onyx was graded on there)

|                          | Handbook  | Onyx    | Basic pipeline |
| ------------------------ | --------- | ------- | -------------- |
| Got the key answer right | 72%       | **77%** | 68%            |
| AI tokens per question   | **4,089** | 50,436  | 2,362          |

**Head to head, question by question** (key answer right, all 150)

| Outcome                              | Questions |
| ------------------------------------ | --------- |
| Both right                           | 64        |
| Only Onyx right                      | 33        |
| Only Handbook right                  | 34        |
| Both wrong                           | 19        |

The overall tie hides a real split. **Each product wins about a third of the questions the other loses**, and on different kinds of question.



## Where Onyx is better than Handbook

Onyx got the key answer right and Handbook did not on **33 questions**. More than half are broad questions:

| Question type                                       | Onyx right, Handbook wrong |
| --------------------------------------------------- | -------------------------- |
| A whole project or customer situation               | 9                          |
| "List everything" about a topic                     | 5                          |
| A big-picture summary                               | 3                          |
| Several facts from one long document                | 4                          |
| Documents disagree, so it must pick the current one | 4                          |
| Other types                                         | 8                          |

**What Onyx does better:**

1. **It gathers many documents for broad questions.**
   - Onyx searches several times with rewritten queries, then has the AI pick the useful sections from about 50 results.
   - On project questions it got 10 of 15 right; Handbook got 2.
   - On "list everything" questions it got 7 of 15; Handbook got 5.
   - On big-picture questions it got 4 of 8; Handbook got 1.
2. **It rarely gives up.** It wrongly said "I don't know" on 3% of questions, against Handbook's 9%. When the answer is in the documents, Onyx keeps looking until it finds it.
3. **It handles conflicting documents slightly better,** getting 14 of 15 such questions right against Handbook's 11. Onyx reads more, so it more often sees both the old and the new version and can tell which is current.
4. **It includes slightly more of the required facts** (70.5% vs 69.7%), because its answers draw on more text.



## Where Handbook is better than Onyx

Handbook got the key answer right and Onyx did not on **34 questions**. In 30 of them, Onyx's answer contained made-up claims.

| Question type                                | Handbook right, Onyx wrong |
| -------------------------------------------- | -------------------------- |
| The answer is not in the documents           | 14                         |
| Narrow questions with conditions             | 6                          |
| Several facts from one long document         | 3                          |
| "List everything" about a topic              | 3                          |
| Mixed, other                                 | 3                          |
| Other types                                  | 5                          |

**What Handbook does better:**

1. **It says "I don't know" when the answer isn't there.** It was right on all 15 unanswerable questions; Onyx was right on 1. See the next section.
2. **It sticks to the documents.** It made up claims in 11% of answers; Onyx did in 47%. When Onyx gets something wrong, it is usually confidently wrong: it invents a cause, a number or a version and presents it as fact.
3. **It is better on narrow questions with conditions**, such as "in a Private deployment…" or "in the March incident…": 10 of 15, against Onyx's 6. On these, Onyx's date filter sometimes threw away every document (see "Observations").
4. **It is cheaper, faster and steadier.**
   - 16× fewer tokens per question.
   - About 2× faster to the first word.
   - Its most expensive question used about 10,000 tokens, against Onyx's 410,000.
5. **It always searches.** Onyx skipped searching entirely on 5 questions and answered from general knowledge. All 5 answers were made up; one gave a watering schedule for a houseplant that contradicted the company's own document.



## Where Handbook lags, and why

We looked at each of the 33 questions that Onyx got right and Handbook got wrong, and sorted them by cause:

| Why Handbook got it wrong                                                                | Questions |
| ---------------------------------------------------------------------------------------- | --------- |
| **Broad question, too few documents read.** It needed 3–8 documents; Handbook used 2–5.  | **17**    |
| **Had the right document, but said "I don't know"**                                      | 6         |
| **Had the right document, but picked the wrong detail** or mixed in another incident     | 5         |
| **Search missed the right document**                                                     | 3         |
| **Documents disagreed, and it presented the outdated one as current**                    | 2         |

**1. Broad questions (17 of 33): the main gap.**

- Handbook gives the AI its 5 best passages from a handful of documents. A project or "list everything" question can need 6–8 documents.
- In these 17 cases Handbook usually found some of the right documents, but not all, so its answer covered part of the topic. For example:
  - Asked which customers had exceptions, it named Northstar Bank but missed Helio Health and QuantaGov.
  - Asked for a rollout's requirements, it missed most of the specific thresholds.
- Onyx's repeated searching and section-picking gather the rest.
- Benchmark 1's follow-up showed that simply giving Handbook 10 or 20 passages did not fix this. What helps is **searching more widely, only for broad questions**.

**2. Too cautious (6 of 33).**

- The right document was in front of the AI, and it still replied "I don't have anything about that".
- This is the same strictness that makes Handbook perfect on unanswerable questions. Here it went too far: on questions with several parts, the AI refused when it could only answer some of them.
- In one of the 6, Claude judged the refusal correct, so the true count may be 5.

**3. Picked the wrong detail (5 of 33).**

- The right document was there, but the answer took a nearby fact. For example, it gave a load-balancer change as the fix instead of the MTU change in the same document.
- Sometimes it mixed in a detail from a similar incident.
- The new "keep each fact with its own document" rule reduced this but did not remove it.

**4. Search misses (3 of 33).** The right document never reached the AI. This is the smallest group.

**5. Outdated information (2 of 33).** Two documents disagreed, and Handbook presented the older version as current.

**What this means:** Handbook's search is not the main problem; it found the right documents more often than Onyx (83% vs 78%). Handbook lags because it **reads too narrowly on broad questions**, and is **sometimes too ready to say "I don't know"**.



## Onyx almost never says "I don't know"

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

- **Onyx's answer instructions ask the model to be helpful,** and gpt-oss fills the gaps from its general knowledge.
- **Handbook has two guards against this.**
  - A confidence check: if nothing matches well enough, it says "I don't know" without calling the AI at all.
  - A strict instruction to answer only from the passages it was given.

**Why it matters:** for a company knowledge tool, a confident wrong answer is worse than "I don't know". The person asking cannot tell the invented contract address from a real one.

**The cost for Handbook:** it wrongly said "I don't know" on 9% of questions whose answer was in the documents, against Onyx's 3% (see "Where Handbook lags").



## Answer quality by question type

Key answer right, all 150 questions:

| What the question asks                                         | Questions | Handbook | Onyx   | Basic pipeline |
| -------------------------------------------------------------- | --------- | -------- | ------ | -------------- |
| One simple fact                                                | 22        | 17       | **19** | 15             |
| A fact, asked in different words than the documents use        | 15        | 11       | **12** | 9              |
| Documents disagree, so it must pick the current or correct one | 15        | 11       | **14** | 11             |
| Several facts from one long document                           | 15        | 11       | **12** | 10             |
| Narrow questions with conditions ("in a Private deployment…")  | 15        | **10**   | 6      | 9              |
| "List everything" about a topic                                | 15        | 5        | **7**  | 4              |
| A whole project or customer situation                          | 15        | 2        | **10** | 3              |
| A big-picture summary                                          | 8         | 1        | **4**  | 1              |
| Mixed, other                                                   | 15        | **15**   | 12     | **15**         |
| The answer is not in the documents                             | 15        | **15**   | 1      | **15**         |

The groups are small (8–22 questions), so one question moves a figure a lot. The two clear patterns:

- **Onyx leads on broad questions:** whole projects, big-picture summaries, and "list everything".
- **Handbook leads where the answer is narrow or missing.**



## Token usage

**AI tokens per question (average)**

```
Handbook        ██                                          4,215
Onyx            ████████████████████████████████████████   69,466
Basic pipeline  █                                           2,374
```

- **Onyx reads far more:** about 68,000 tokens per question against Handbook's 3,800. Onyx makes about 12 AI calls per question, and one of them reads around 50 search results.
- **Onyx's cost swings widely.** Its most expensive answer used **409,561 tokens across 64 AI calls**. Handbook's most expensive used 10,452 tokens in 7 calls.
- **Handbook uses a little more than in Benchmark 1** (4.2k vs 3.7k on average), because of two of the new settings:
  - the up-front rewording adds a small AI call to every question;
  - the neighbouring text makes each answer read a little more.

  It is still 16× below Onyx.
- **No runaway "thinking" this time.** At low reasoning effort, gpt-oss thinks for only about 15–30 tokens a call. In Benchmark 1, three Onyx calls hit the model's 131,000-token limit without answering.



## Latency

|                                 | Handbook  | Onyx   | Basic pipeline |
| ------------------------------- | --------- | ------ | -------------- |
| Time to first word, typical     | **6.2 s** | 11.2 s | 2.4 s          |
| Time to first word, slowest 10% | **10 s**  | 35 s   | 4.5 s          |

- **Both products are faster than in Benchmark 1** (Handbook 9.7 s and Onyx 41.5 s there), because gpt-oss at low effort thinks much less than Nemotron did.
- **Handbook is about 2× faster on a typical question and 3.5× faster on the slowest ones.**
- Times exclude free-service queuing.



## Checking the reviewer

Benchmark 1 taught us not to trust one AI reviewer blindly: Gemini called 5× too many answers "made up". So before grading all 450 answers with Gemma, we had Claude grade the same 45 answers (15 per system, the same sample used in Benchmark 1) and compared the two.

|                                       | Handbook | Onyx  | Basic | All             |
| ------------------------------------- | -------- | ----- | ----- | --------------- |
| Reviewers agree on "key answer right" | 14/15    | 13/15 | 13/15 | **40/45 (89%)** |
| Key answer right, according to Claude | 12       | 10    | 9     | 31              |
| Key answer right, according to Gemma  | 11       | 8     | 7     | 26              |
| Answers with a made-up claim, Claude  | 1        | 6     | 2     | 9               |

- **Gemma is reliable for "is the answer right?"**
  - All 5 disagreements were Gemma being stricter than Claude.
  - 3 of them were honest "I don't know" answers that Gemma marked wrong.
  - So Gemma's accuracy figures, if anything, slightly undercount every system.
- **Gemma is less reliable for "what exactly was made up".**
  - It flagged about as many answers as Claude (8 vs 9), but only 3 were the same answers.
  - Both reviewers agree on the direction: Onyx invents most. Claude flagged 6 of 15 Onyx answers, 1 of 15 Handbook answers and 2 of 15 basic answers.
  - The exact made-up percentages should wait for Claude's full review (see next steps).



## Observations

What we actually saw during the run.

**About the products**

1. **The accuracy gap from Benchmark 1 did not hold with a different model.**
   - On all 150 questions, Handbook and Onyx tie (65% each).
   - On Benchmark 1's 60 questions, Onyx's lead shrank from 7 points to 5.
   - **Accuracy depends on the model at least as much as on the product.**
2. **Onyx answers almost everything, including what it doesn't know.** It answered 14 of the 15 unanswerable questions with invented details, and said "I don't know" only 5 times in 150.
3. **Onyx sometimes skips the search entirely.** On 5 questions, its model decided no search was needed and answered from general knowledge. All 5 were graded as made up.
4. **Onyx's date filter can throw away every document.**
   - **What happened:** on 8 questions, Onyx's model turned a date in the question into a search filter that kept only documents updated in that period. Examples: "the March 2026 incident" → only March 2026; "the 2026-01-15 game day" → only that day; "H1 2025".
   - **The effect:** our test documents carry different dates, so the search came back empty.
     - In 4 of the 8, Onyx said it had nothing.
     - In the other 4, it answered anyway and was graded as making things up.
   - **Why the 8 count:** Benchmark 1's model did not do this. This is Onyx's own behaviour, so these answers count.
   - **Without them,** Onyx gets the key answer right on 68.3% of the remaining 142 questions.
   - **How we confirmed it:** we re-asked these questions twice, the second time with a minute between them, and they failed the same way each time. Onyx's own log shows the date filter on every one.
5. **Onyx often attaches a real fact to the wrong thing.** 15 of its answers took a real fact from one document and presented it as belonging to another, against 7 for Handbook. Reading 50 results per question brings more chances to mix them up.
6. **Handbook's guards work, but are sometimes too strict.**
   - The confidence check and the strict answer instructions gave it a perfect score on unanswerable questions and few made-up claims.
   - The same guards made it wrongly refuse 9% of answerable questions.
7. **Handbook's weakness is broad questions, not search.** It found the right documents more often than Onyx (83% vs 78%), but reads too few of them for project and "list everything" questions.
8. **Handbook beats the basic pipeline,** but by less than in Benchmark 1 (65% vs 61% key answer right). Its extra steps help most on simple facts and differently-worded questions.

**About running the benchmark**

1. **Finding a free model took several tries.** We have no paid account, so only free services were possible.
   - **Mistral:** needs credit added before its API works.
   - **NVIDIA:** its free service would not serve the non-NVIDIA models we tried; they failed or timed out.
   - **Gemini:** the free tier allows only 20 requests a day per project, and its newer models cannot switch "thinking" off.
   - **Ollama Cloud worked.** Its free plan serves gpt-oss-120b and Gemma 4 with a monthly allowance, and the whole run used only a small part of it.
2. **gpt-oss cannot switch thinking off completely.** At "low" effort it thinks for 15–30 tokens a call; those tokens are counted in the figures above.
3. **The Jina search key ran out of credit mid-run.**
   - Handbook stopped itself after 3 failed questions. Onyx kept going and answered some questions with no documents.
   - We switched both products to a backup Jina key and resumed.
   - Handbook's 3 failed questions were asked again and answered normally.
   - Onyx's affected questions were re-asked; 3 recovered.
4. **Jina's free tier rate-limits bursts.** Onyx sends 5–8 searches at once for each question, and Jina sometimes rejects part of the burst. Onyx retries these, so the answers were not affected.
5. **We ran the systems in parallel to finish in one day.**
   - Onyx ran on one Ollama account; Handbook and then the basic pipeline ran on a second.
   - Each system had its own logging proxy, so token counts were never mixed.
   - All 450 answers finished with no errors.
6. **The reviewer occasionally replied in the wrong format.** In 16 of about 2,600 per-fact verdicts, Gemma wrote "true" as text or wrapped it in an object. These were read as plain yes/no.



## Limits of this run

- **It's still a pilot:** 1,374 documents, not the full 500,000. Search is easier with less noise.
- **Each question was asked once,** so there is no check on consistency between runs. With 150 questions a percentage can be off by about ±8 points, so a gap of 1 point is a tie.
- **One reviewer graded everything (Gemma).** Claude checked only a 45-answer sample. The made-up figures in particular need a full Claude review.
- **The model and the reviewer both changed from Benchmark 1,** so the two benchmarks' figures should be compared with care. In particular, we can't yet say how much Handbook's new settings helped on their own.
- **Free services only.** Jina's limits forced retries, and some Onyx answers were affected by its date filter.



## Recommended next steps

1. **Have Claude grade all 450 answers.** This is the next step.
   - **Why:** Gemma is reliable for "is the answer right?", but not for exactly which claims were made up. Claude's grading was the one we trusted in Benchmark 1.
   - **How:** the same rules as Benchmark 1.
     - Claude reads each answer next to the exact text the system was shown.
     - It searches that text for every name, number and date in the answer.
     - It counts a claim as made up only if it appears nowhere.
     - A real fact attached to the wrong thing counts as a wrong answer, not as made up.
   - **What it gives us:**
     - final, trustworthy made-up figures for all three systems;
     - a second opinion on accuracy for every answer, not just 45;
     - the final numbers for this doc.
   - **Expected outcome:** the 45-answer sample suggests accuracy will stay about the same, with Gemma slightly stricter. The made-up gap between Onyx and Handbook should remain, though the exact percentages may change.
2. **Improve Handbook on broad questions.** This is its main gap: 17 of the 33 questions it lost to Onyx. Options to test:
   - detect broad questions (project, "list everything", summary) and read more documents only for those, keeping the 5-passage default for everything else;
   - search again with new wording when the first pass covers only part of the topic;
   - group passages by project, so the AI sees the whole picture.
3. **Make Handbook less ready to say "I don't know".** On 6 lost questions the answer was in front of it. Options:
   - let it answer the parts it can and say clearly which part is missing;
   - check whether the confidence threshold or the answer instructions cause the refusals.
4. **Tune the weak-result cutoff** from the scores recorded in this run (`raw.rerank_scores` in Handbook's records). Choose the value that removes misleading passages without dropping useful ones.
5. **Measure each new Handbook setting on its own,** with the same model and reviewer, to see which ones actually help and which only add tokens.
6. **Run the full benchmark** (all 500 questions and the full document set) once these changes are in.

---

The raw data is in `evaluation/reports/bench2/`:

- answers in `runs/`;
- Gemma's grades in `reviews_v2/`;
- Claude's 45-answer check in `reviews_claude/`.

The run script is `evaluation/erb/run_bench2.sh`.
