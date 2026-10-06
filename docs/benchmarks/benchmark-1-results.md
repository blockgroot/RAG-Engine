# Handbook vs Onyx: what the benchmark showed

*Benchmark 1 (token use and context management) · Pilot run, 5–6 October 2026*

Both products got the same 150 test questions, the same 1,374 documents and the same AI model. We self-hosted Onyx from its official release (v4.8.4) and did not modify it.

## In short

- **Handbook is far cheaper.** On a typical question it used about **13× fewer AI tokens** (3.2k vs 43k). Per correct answer it used about **16× fewer**.
- **Handbook is about 4× faster** to the first word (about 10 s vs 42 s), and its timing is much more predictable.
- **Onyx gives more correct answers**: 72% wins vs 53% for Handbook, on the same 60 questions.
- **Both stick to the documents.** Made-up claims are rare in every system: 2% of Handbook's answers, 5% of Onyx's, on the same questions. The gap between them is accuracy, not invention.
- **Handbook beats the basic pipeline** (53% vs 42% wins, fewer made-up claims), so its extra steps pay off.



## What we tested

- **Questions:** 200 questions from *EnterpriseRAG-Bench*, a public benchmark made by Onyx. They cover 10 types: simple facts, reasoning, documents that disagree, "list everything", questions with no answer, and others. The results below use the 150 test questions. We asked 50 of them three times to check consistency.
- **Documents:** 1,374 made-up company documents (Slack, email, tickets, docs). They include the right documents for every question, plus 1,000 unrelated ones as noise. The full benchmark has about 500,000 documents, so this run is a pilot.
- **Systems:**
  - **Handbook:** our real answer pipeline.
  - **Onyx:** its real code.
  - **Basic pipeline:** the simplest possible setup, built only as a yardstick. It runs a plain search over the same documents, takes the top 10 passages, and asks the AI to answer from them in one call. There is no reranking, no confidence check, and no extra AI steps. If a real product can't beat this, its extra steps aren't paying off.
- **Fairness:**
  - Both products used the same AI model (NVIDIA Nemotron 3 Super) and the same search embeddings (Jina).
  - Every AI call went through one logging proxy, so tokens were counted the same way for every system.
- **Grading:**
  - An AI reviewer (Anthropic Claude) checked each answer against the correct answer and against the exact documents the system saw. It looked for every name, number and date in the answer.
  - It graded all 150 Handbook and basic answers, and 60 of Onyx's (a usage limit cut Onyx short). The 60 Onyx questions cover only 5 of the 10 types, and none of the hardest ones. So **quality is compared on those same 60 questions for all three systems.**
  - A first reviewer (Google Gemini) proved too strict about "made up" (see Groundedness), so its grades are not used for the final quality figures.



## Results at a glance

**Cost and speed (all 150 questions)**

|                                          | Handbook    | Onyx    | Basic pipeline |
| ---------------------------------------- | ----------- | ------- | -------------- |
| AI tokens per question (typical)         | **3,231**   | 43,075  | 2,920          |
| AI calls per question (typical)          | **2**       | 8       | 1              |
| Time to first word (typical)             | **9.7 s**   | 41.5 s  | 7.2 s          |
| Found the right documents                | 80%         | 82%     | 80%            |

**Quality (the same 60 questions, graded for all three)**

|                                          | Handbook    | Onyx    | Basic pipeline |
| ---------------------------------------- | ----------- | ------- | -------------- |
| Wins (correct + honest "I don't know")   | 53%         | **72%** | 42%            |
| Got the key answer right (details aside) | 73%         | **80%** | 70%            |
| Made-up claim (not in the documents)     | **2%**      | 5%      | 3%             |
| Wrongly said "I don't know"              | 7%          | **3%**  | 10%            |
| AI tokens per correct answer             | **6,261**   | 97,199  | 8,033          |
| Cost per correct answer (at paid prices) | **$0.0011** | $0.0175 | $0.0016        |

- "Wins" counts answers that are fully correct and stick to the documents, plus correct "I don't know" replies when the answer really isn't there.
- "Got the key answer right" is looser: the core of the answer matches the correct answer, even if some details are missing.
- "Typical" means the median, the middle question.
- "Cost at paid prices" uses the model's published price: $0.09 per million input tokens and $0.45 per million output tokens. The run itself was free.
- With 60 questions, a win rate can be off by about ±12 points. Onyx's lead over Handbook (19 points) is larger than that; treat the exact figures as approximate.



## Token usage

AI models are paid by the token. Handbook used about **3,200 tokens per question**; Onyx used about **43,000**.

**AI tokens per correct answer** (same 60 questions)

```
Handbook        ███                                         6,261
Onyx            ████████████████████████████████████████   97,199
Basic pipeline  ███                                         8,033
```

- **Most of Onyx's tokens are spent reading, not writing.** Onyx reads about 39,000 tokens per question; Handbook reads about 2,400. That comes from Onyx's design (see "Why Onyx makes so many AI calls" below), so it would be similar on any AI model.
- **Onyx's cost varies a lot from question to question.** For the most expensive 10% of questions, Onyx used 273,000 tokens or more; the same figure for Handbook is 5,300. On hard questions Onyx's agent searched up to 5 times, and one answer read about 283,000 tokens.
- **Three Onyx answers got stuck "thinking".** In each, one AI call kept reasoning until it hit the model's 131,000-token limit without producing an answer. Onyx puts no limit on that call; Handbook limits its answer call.



## Answer quality

On the 60 questions graded for all three systems, Onyx gives more correct answers: **72% wins vs 53%** for Handbook and 42% for the basic pipeline. If we only ask "did it get the key answer right?", the gap narrows: **80% vs 73%**. Much of Handbook's shortfall is answers that get the main point but miss some required details. Onyx also wrongly said "I don't know" less often (3% vs 7%).

**By question type (wins, same 60 questions)**

| What the question asks                                           | Questions | Handbook | Onyx      | Basic pipeline |
| ---------------------------------------------------------------- | --------- | -------- | --------- | -------------- |
| One simple fact                                                  | 22        | 15       | **17**    | 9              |
| A fact, asked in different words than the documents use          | 15        | 6        | **12**    | 4              |
| Documents disagree, so it must pick the current or correct one   | 8         | 4        | **6**     | 4              |
| Several facts from one long document (a meeting, a negotiation)  | 8         | 4        | **5**     | 4              |
| About one specific event or date ("in the Feb 12 incident…")     | 7         | 3        | 3         | **4**          |

The groups are small, so one question moves a figure a lot. Onyx's clearest lead is on questions **worded differently from the documents**, where its question rewriting helps.

**Not covered for Onyx:** the other five types, including the hardest ("list everything", big-picture, customer or project situations, small details, and questions with no answer). Across all 150 questions, Handbook won 47% and the basic pipeline 41%. On questions with no answer in the documents, both correctly said "I don't know" 87% of the time. In the first reviewer's grades, Onyx's lead was largest on the "list everything", big-picture and project types, but that has not been confirmed by the final review.

**Consistency:** we asked 50 of the questions three times. Token use was very steady for Handbook (3,700–3,790 per question across runs) and much less so for Onyx (85,000–101,000). Graded by the first reviewer, Handbook's win rate stayed between 42% and 48% across runs; Onyx's ranged from 50% to 66%.

## Groundedness (did the answer stick to the documents?)

The reviewer listed every specific claim in each answer (names, numbers, dates, owners, decisions) and searched the exact documents the system was shown. A claim counted as "made up" only if it appeared nowhere in them. A real fact attached to the wrong thing counted as a wrong answer, not an invented one.

|                                       | Handbook | Onyx | Basic pipeline |
| ------------------------------------- | -------- | ---- | -------------- |
| Made up, same 60 questions            | **2%**   | 5%   | 3%             |
| Made up, all graded answers           | 7% (150) | 5% (60) | 10% (150)   |
| Fully grounded, same 60 questions     | **98%**  | 95%  | 97%            |

**All three systems are well grounded.** When an answer is wrong, it is almost always because the system missed or mixed up a fact, not because it invented one.

**Why the earlier figures were different:** the first reviewer (Gemini) flagged 47% of Handbook's answers as made up. On the 360 answers both reviewers graded, Gemini flagged 143 and Claude 28. Most of Gemini's flags were claims that were in the documents word for word. The two reviewers agreed on whether the key answer was right 87% of the time, so the first reviewer's accuracy picture was roughly right. Only its "made up" figures were wrong.



## Latency


|                                 | Handbook  | Onyx   | Basic pipeline |
| ------------------------------- | --------- | ------ | ----- |
| Time to first word, typical     | **9.7 s** | 41.5 s | 7.2 s |
| Time to first word, slowest 10% | **23 s**  | 267 s  | 20 s  |


- Handbook answers in about 10 seconds and rarely takes much longer. Onyx usually takes about 40 seconds; on hard questions it took 4 to 28 minutes.
- **These times are slower than real use for both products, and more so for Onyx.**
  - The AI model we used "thinks" before every answer, and it ran on a free service.
  - Onyx makes many more AI calls, so it pays that thinking time many more times.
  - With fast paid models, both products would be quicker and the gap smaller. Onyx publishes an average of about 35 seconds.
  - None of this affects the token comparison.
- We removed free-service waiting time from every timing: time spent queued by a provider, or on a call that hung.



## Why Onyx makes so many AI calls

The two products are built differently. **Onyx is an AI agent**: it asks the AI to make each decision along the way. **Handbook makes most of those decisions in ordinary code**, and uses the AI mainly to write the final answer.

**Onyx, for a typical question (about 7–8 calls)**


| AI call                              | What it does                                                          | Why it's there                                                                                                                      |
| ------------------------------------ | --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| 1. Should I search?                  | Decides whether the documents are needed                              | Onyx is a general chat assistant. Some messages ("rewrite this email") need no search                                               |
| 2–3. Rewrite the question            | Turns the question into one or two search queries                     | Conversational questions search poorly; two phrasings find more                                                                     |
| 4. Date filter?                      | Decides whether to limit the search by date                           | Handles questions like "last week's incident"                                                                                       |
| 5. Pick the useful sections          | Reads about 32 search results (about 27,000 tokens) and picks about 2 | Onyx's main quality bet: the AI judges relevance with real understanding. This one call accounts for most of Onyx's tokens and time |
| 6. Relevance check                   | Double-checks the picked sections and extracts the useful parts       | Avoids answering from text that only looked relevant                                                                                |
| 7. Write the answer, or search again | Writes the answer, or goes back to step 2                             | Lets Onyx gather more on hard questions. In this run it went up to 5 rounds and 43 calls                                            |


**Handbook, for a typical question (about 2 calls)**


| Step                      | AI call?                     | What it does                                                                                                            |
| ------------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Search                    | No                           | Combines meaning search and keyword search. A ranking model (not a chat AI) picks the best 5 passages in about a second |
| Confidence check          | No                           | If nothing matches well enough, says "I don't know" without calling the AI                                              |
| Write the answer          | Yes (1 call)                 | Writes the answer strictly from those 5 passages                                                                        |
| Tone check                | Yes (1 small call)           | Runs at the same time as the answer, and adds a friendly opening when needed                                            |
| Split a two-part question | Sometimes (31% of questions) | Searches each part separately                                                                                           |
| Retry a failed search     | Sometimes (9% of questions)  | Tries alternative wording once before giving up                                                                         |


**Steps 2 to 7 can repeat.** If the AI isn't satisfied with what it found, Onyx searches again: new queries, a new pick, a new check. It can go round about 5 times, so a hard question can take 20 to 40 AI calls instead of 7. That loop is why Onyx's token use and time swing so much from question to question.

**What this means:**

- **Onyx** spends AI tokens to be flexible and thorough. It reads widely, uses the AI to judge relevance, and searches again when unsure. That buys accuracy on questions that need several documents, at about 13× the tokens and 4× the time.
- **Handbook** does the same steps in code, so it is cheap, fast and predictable. It gives the AI 5 short passages. A follow-up test (below) showed that giving it 10 or 20 does not close the gap, so Onyx's edge comes from how it works the question, not from reading more.



## Observations

1. **Handbook wins clearly on cost and speed.** It uses about 16× fewer tokens per correct answer, is about 4× faster, and stays steady from question to question.
2. **Onyx wins on accuracy:** 72% vs 53% wins on the same 60 questions. The gap is smaller (80% vs 73%) for getting the key answer right; Handbook more often leaves out details.
3. **Neither product invents much.** Made-up claims appear in 2–5% of answers on the shared questions. Groundedness is not what separates them.
4. **The accuracy gap is not about finding documents, or about how much Handbook reads.** On the shared questions, Handbook found the right documents slightly more often than Onyx (88% vs 81%). Handbook passes 5 short passages and Onyx reviews about 32, but giving Handbook 10 or 20 passages did not clearly raise its accuracy (see the follow-up below). The likelier causes are Onyx's question rewriting and repeated searches, and how the model builds long answers with many parts.
5. **Handbook beats the basic pipeline:** 53% vs 42% wins on the shared questions (47% vs 41% on all 150), and fewer made-up claims (7% vs 10%). Its extra steps (reranking, the confidence check, question splitting) cost about 10% more tokens and pay for themselves.
6. **Onyx has reliability problems under load:**
   - **Its search crashed when the embedding service rate-limited it,** because of a bug in Onyx's error handling. That left 9 test answers written with no documents.
   - **Some of its AI calls never finished thinking.** In 3 answers, the AI kept reasoning until it hit the model's maximum output (about 131,000 tokens) and never gave a result. Onyx puts no limit on that call, so it can't stop it; Handbook limits its answer call.
   - **Its slowest answers took up to 28 minutes.** That happened when the agent searched again and again, and was made worse by our slow free model. A customer on a fast paid model would see much shorter times, but the extra searching would still cost tokens.
7. **AI reviewers need checking.** Our first reviewer called 5× too many answers "made up". A second reviewer was what caught it.



## Follow-up: does Handbook do better if it reads more?

Since both products find the right documents about equally often, we tested whether Handbook would match Onyx if it simply gave the AI more to read. We ran Handbook on the 50 separate tuning questions with 10 and 20 passages instead of 5. Everything else stayed the same: the AI model, search and settings. Claude graded all three settings the same way.

|                                   | Usual (5) | 10 passages | 20 passages |
| --------------------------------- | --------- | ----------- | ----------- |
| AI tokens per question (typical)  | **3,407** | 5,133       | 7,419       |
| AI tokens per correct answer      | **8,561** | 12,039      | 17,613      |
| Found the right documents         | 86%       | 89%         | **92%**     |
| Wins                              | 44%       | **50%**     | 46%         |
| Got the key answer right          | 76%       | **80%**     | 78%         |
| Made-up claim                     | 8%        | **4%**      | 8%          |
| Time to first word (typical)      | **13 s**  | 15 s        | 19 s        |

- **Reading more did not clearly help.** Wins moved by about 3 questions out of 50, which is within normal variation. Going from 5 to 20 passages, 4 questions became correct and 3 stopped being correct.
- **The questions it was meant to fix stayed unsolved.** "List everything" questions stayed at 0 of 5 in every setting, and project questions at 0–1 of 5.
- **The cost doubled.** Tokens per correct answer went from about 8,600 to 17,600, and answers started about 6 seconds later.
- **It did not make Handbook invent more.** Made-up claims stayed at 4–8%.
- **10 passages showed a small hint of improvement,** but too small to trust on 50 questions.

**What it means:** Handbook's usual 5 passages are not what holds it back. To close the gap with Onyx, the next things to look at are how the question is searched (rewriting it, searching more than once) and how the model writes answers that need many parts.



## Limits of this run

- **It's a pilot:** 1,374 documents, not the full 500,000. Search is easier with less noise, so every accuracy figure here is higher than it would be at full scale.
- **We used one AI model, on free services.** The model "thinks" before answering, which inflates response times, and Onyx's more than Handbook's. The token counts for reading are not affected.
- **Onyx's quality was graded on 60 of 150 answers,** covering only 5 question types. Quality figures for the hardest types are Handbook and basic only. No person has checked the grades yet.
- **Some features were off in both products.**
  - **Handbook:** connected tools, the knowledge graph, personal memory and web search were off, because the benchmark is plain files.
  - **Onyx:** web search and deep-research mode were off.



## Recommended next steps

1. **Grade the remaining 90 Onyx answers** so the hardest question types can be compared too.
2. **Test smarter searching, not more reading.** Reading more did not help (see the follow-up). Try rewriting the question into a few search queries and searching again when the first pass is thin, on the tuning questions, to see how much of Onyx's accuracy Handbook can match while staying far cheaper. Optionally confirm the small 10-passage gain on the 150 test questions.
3. **Repeat the run with a fast, non-thinking model,** to get speed numbers closer to what customers would see.
4. **Run the full benchmark** (all 500 questions and the full document set) once we've acted on the pilot findings.

---

The raw data, the detailed report (`REPORT.md`), the final reviews (`reviews_claude/`, and `reviews_claude_dev/` for the follow-up) and the run notes (`notes.md`) are in `evaluation/reports/bench1/`.