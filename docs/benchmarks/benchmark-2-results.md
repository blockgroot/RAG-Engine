# Handbook vs Onyx, second run: a different AI model

*Benchmark 2 (accuracy re-check with a different model) · 7 October 2026*

Benchmark 1's accuracy figures were not convincing on their own. So we ran the same test again with a different AI model, to see whether the results hold.

We kept everything else as close as possible:

- the same 150 test questions;
- the same 1,374 documents;
- the same unmodified Onyx (v4.8.4).

Handbook ran with the accuracy changes that came out of Benchmark 1:

1. **Reads the surrounding text.** For its two best passages, Handbook also reads the paragraph just before and just after, so an answer split across paragraphs isn't cut in half.
2. **Keeps each fact with its own document.** A new rule tells the AI never to take a fact from one document and present it as belonging to another, for example mixing up two similar incidents.
3. **Searches with other wordings from the start.** Before searching, Handbook asks the AI for two other ways to phrase the question and searches with all three. This finds documents that use different words than the person asking. Before, it only tried other wordings after a search failed.
4. **Can drop weak results.** A cutoff can remove passages that score far below the best one. It was **off** in this run; its scores were recorded so the right value can be chosen later.

## In short

- **With this model, Handbook and Onyx are equally accurate.** Each got the key answer right on about 65% of the 150 questions (Handbook 65.3%, Onyx 64.7%). In Benchmark 1, Onyx led by 7 points on the same measure.
- **Handbook is still far cheaper and faster:** about **16× fewer AI tokens** per question (4.2k vs 69k), and **about 2× faster** to the first word (6 s vs 11 s).
- **Onyx almost never says "I don't know".** It did so in only 5 of 150 answers.
  - On the 15 questions whose answer is not in the documents, Onyx answered 14 anyway, with invented details.
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

## Where Onyx is better than Handbook

Onyx got the key answer right and Handbook did not on 33 questions. More than half of them were broad questions.

- **Broad questions.** These are questions about a whole project, a big-picture summary, or "list everything" on a topic.
  - Onyx searches several times with rewritten queries, then has the AI pick the useful sections from about 50 results.
  - That gathers the many documents these questions need.
  - On project questions it got 10 of 15 right; Handbook got 2.
- **It rarely gives up when the answer exists.** Onyx wrongly said "I don't know" on 3% of questions, against Handbook's 9%.
- **Documents that disagree.** Onyx got 14 of 15 such questions right; Handbook got 11. Because it reads more, Onyx more often sees both the old and the new version and can tell which is current.
- **Slightly more of the required facts** (70.5% vs 69.7%), because its answers draw on more text.



## Where Handbook is better than Onyx

Handbook got the key answer right and Onyx did not on 34 questions. In 30 of them, Onyx's answer contained made-up claims.

- **It says "I don't know" when the answer isn't there.** On the 15 questions with no answer in the documents, Handbook said so every time. Onyx said so once, and invented an answer for the other 14.
- **It sticks to the documents.** Made-up claims appeared in 11% of Handbook's answers, against 47% of Onyx's. When Onyx is wrong it is usually confidently wrong, which is riskier than "I don't know".
- **Why the difference:**
  - Handbook has a confidence check that says "I don't know" without calling the AI when nothing matches well enough.
  - It also has a strict instruction to answer only from the passages it was given.
  - Onyx's answer instructions ask the model to be helpful, and gpt-oss fills the gaps from its general knowledge.
- **Narrow questions with conditions.** Handbook got 10 of 15 right; Onyx got 6. On some of these, Onyx's date filter removed every document (see "Observations").
- **It always searches.** Onyx skipped the search entirely on 5 questions and answered from general knowledge; all 5 answers were made up.
- **It is cheaper, faster and steadier.** It uses 16× fewer tokens per question and is about 2× faster to the first word. Its most expensive question used about 10,000 tokens, against Onyx's 410,000.



## Where Handbook lags, and why

We looked at each of the 33 questions that Onyx got right and Handbook got wrong.

- **Broad questions (17 of 33): the main gap.**
  - Handbook gives the AI its 5 best passages from a handful of documents, while these questions can need 6–8 documents.
  - It usually found some of the right documents but not all, so its answer covered only part of the topic.
  - Benchmark 1's follow-up showed that simply reading 10 or 20 passages does not fix this; searching more widely for broad questions should.
- **Too cautious (6 of 33).**
  - The right document was in front of the AI, but it still said "I don't know", usually on questions with several parts that it could only partly answer.
  - It is the same strictness that makes Handbook reliable on unanswerable questions, taken too far.
- **Picked the wrong detail (5 of 33).**
  - The right document was there, but the answer used a nearby fact, or mixed in a detail from a similar case.
  - The new "keep each fact with its own document" rule reduced this but did not remove it.
- **Search missed the right document (3 of 33).** The right document never reached the AI.
- **Outdated information (2 of 33).** Two documents disagreed, and Handbook presented the older version as current.

**Overall:** search is not Handbook's main problem; it found the right documents more often than Onyx (83% vs 78%). It lags because it reads too narrowly on broad questions and is sometimes too quick to say "I don't know".

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
   - **What happened:** on 8 questions, Onyx's model turned a date in the question into a search filter that kept only documents updated in that period.
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
