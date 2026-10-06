# Handbook vs Onyx: what the benchmark showed

*Benchmark 1 (token use and context management) · Pilot run, 5–6 October 2026*

Both products got the same 150 test questions, the same 1,374 documents and the same AI model. We self-hosted Onyx from its official release (v4.8.4) and did not modify it.

## In short

- **Handbook is far cheaper.** On a typical question it used about **13× fewer AI tokens** (3.2k vs 43k). Per correct answer it used about **18× fewer**.
- **Handbook is about 4× faster** to the first word (about 10 s vs 42 s), and its timing is much more predictable.
- **Onyx gives more correct answers**: 59% wins vs 46% for Handbook. Onyx is strongest on questions that need several documents.
- **Both find the right documents about equally often** (80% vs 82%). Onyx's lead comes from reading more of what it finds, not from finding more.
- **The "made up" numbers aren't reliable yet.** A second reviewer confirmed only 5 of 23 "made up" flags, so we are re-checking them.

## What we tested

- **Questions:** 200 questions from *EnterpriseRAG-Bench*, a public benchmark made by Onyx. They cover 10 types: simple facts, reasoning, documents that disagree, "list everything", questions with no answer, and others. The results below use the 150 test questions. We asked 50 of them three times to check consistency.
- **Documents:** 1,374 made-up company documents (Slack, email, tickets, docs). They include the right documents for every question, plus 1,000 unrelated ones as noise. The full benchmark has about 500,000 documents, so this run is a pilot.
- **Systems:**
  - **Handbook:** our real answer pipeline.
  - **Onyx:** its real code.
  - **A basic baseline:** plain search plus one AI call, as a reference point.
- **Fairness:**
  - Both products used the same AI model (NVIDIA Nemotron 3 Super) and the same search embeddings (Jina).
  - Every AI call went through one logging proxy, so tokens were counted the same way for every system.
- **Grading:**
  - One AI reviewer (Google Gemini) checked every answer against the correct answer and against the documents the system actually saw.
  - A second reviewer (Anthropic Claude) re-checked a 10% sample.

## Results at a glance

| | Handbook | Onyx | Basic baseline |
| --- | ---: | ---: | ---: |
| AI tokens per question (typical) | **3,231** | 43,075 | 2,920 |
| AI tokens per correct answer | **7,988** | 146,856 | 7,174 |
| Cost per correct answer (at paid prices) | **$0.0015** | $0.0233 | $0.0015 |
| AI calls per question (typical) | **2** | 8 | 1 |
| Time to first word (typical) | **9.7 s** | 41.5 s | 7.2 s |
| Found the right documents | 80% | 82% | 80% |
| Wins (correct + honest "I don't know") | 46% | **59%** | 45% |
| Main point correct | 59% | **80%** | 63% |
| Wrongly said "I don't know" | **1%** | 5% | 3% |

- "Typical" means the median, the middle question.
- "Cost at paid prices" uses the model's published price: $0.09 per million input tokens and $0.45 per million output tokens. The run itself was free.

## Token usage

AI models are paid by the token. Handbook used about **3,200 tokens per question**; Onyx used about **43,000**.

**AI tokens per correct answer**

```
Handbook        ██                                          7,988
Onyx            ████████████████████████████████████████  146,856
Basic baseline  ██                                          7,174
```

- **Most of Onyx's tokens are spent reading, not writing.** Onyx reads about 39,000 tokens per question; Handbook reads about 2,400. That comes from Onyx's design (see "Why Onyx makes so many AI calls" below), so it would be similar on any AI model.
- **Onyx's cost varies a lot from question to question.** For the most expensive 10% of questions, Onyx used 273,000 tokens or more; the same figure for Handbook is 5,300. On hard questions Onyx's agent searched up to 5 times, and one answer read about 283,000 tokens.
- **Three Onyx answers got stuck "thinking".** In each, one AI call kept reasoning until it hit the model's 131,000-token limit without producing an answer. Onyx puts no limit on that call; Handbook limits its answer call.

## Answer quality

Onyx gives more correct answers overall: **59% wins vs 46%**. Its main point is right **80%** of the time, against 59% for Handbook. Handbook is better at not refusing by mistake: it wrongly said "I don't know" only 1% of the time, against 5% for Onyx.

**By question type (wins)**

| Question type | Handbook | Onyx | Basic |
| --- | ---: | ---: | ---: |
| Simple fact | 64% | 64% | 50% |
| Same fact, asked differently | 47% | **67%** | 47% |
| Reasoning within one document | 53% | **67%** | 53% |
| With conditions ("only the Q3 one") | **47%** | 33% | 60% |
| Documents that disagree | 40% | **67%** | 20% |
| "List everything about X" | 7% | **33%** | 13% |
| Big-picture questions | 12% | **50%** | 12% |
| About a project | 0% | **33%** | 7% |
| Answer is not in the documents | **100%** | 93% | 100% |
| Mixed | 67% | 73% | 73% |

Each type has about 15 questions, so one question moves a type's score by about 7 points. Read these as patterns, not exact figures.

Onyx's lead is largest where the answer needs **several documents**: "list everything", big-picture and project questions. On simple facts the two products tie.

**Consistency:** we asked 50 of the questions three times. For most of them, each system gave the same kind of result in all three runs: Handbook 68% of questions, Onyx 60%, basic 70%. Across the three runs, Handbook's win rate stayed between 42% and 48%; Onyx's ranged from 50% to 66%.

## Groundedness (did the answer stick to the documents?)

We checked whether answers made claims the documents didn't support. Here the grading itself turned out to be the problem.

| | Handbook | Onyx | Basic |
| --- | ---: | ---: | ---: |
| "Made up", first reviewer (all 150 answers) | 47% | 34% | 33% |
| "Made up", first reviewer, 15-answer sample | 7 of 15 | 11 of 15 | 5 of 15 |
| "Made up", second reviewer, same 15 answers | 2 of 15 | 4 of 15 | 0 of 15 |

> **Don't quote the "made up" rates above yet.** The second reviewer confirmed only 5 of the first reviewer's 23 "made up" flags. Most of the flagged claims were actually in the documents. In several cases, the answer had used a real fact from the wrong document, which makes the answer wrong, not invented. The two reviewers agreed on the overall verdict for only 51% of the sample. They broadly agreed on which answers were correct, so the accuracy results above stand.

**Next step:** tighten the reviewer's instructions, then re-review all answers. The new instructions:

- The reviewer must quote the passage behind any "unsupported" verdict.
- "Took a fact from the wrong document" counts as wrong, not made up.

## Latency

| | Handbook | Onyx | Basic |
| --- | ---: | ---: | ---: |
| Time to first word, typical | **9.7 s** | 41.5 s | 7.2 s |
| Time to first word, slowest 10% | **23 s** | 267 s | 20 s |

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

| AI call | What it does | Why it's there |
| --- | --- | --- |
| 1. Should I search? | Decides whether the documents are needed | Onyx is a general chat assistant. Some messages ("rewrite this email") need no search |
| 2–3. Rewrite the question | Turns the question into one or two search queries | Conversational questions search poorly; two phrasings find more |
| 4. Date filter? | Decides whether to limit the search by date | Handles questions like "last week's incident" |
| 5. Pick the useful sections | Reads about 32 search results (about 27,000 tokens) and picks about 2 | Onyx's main quality bet: the AI judges relevance with real understanding. This one call accounts for most of Onyx's tokens and time |
| 6. Relevance check | Double-checks the picked sections and extracts the useful parts | Avoids answering from text that only looked relevant |
| 7. Write the answer, or search again | Writes the answer, or goes back to step 2 | Lets Onyx gather more on hard questions. In this run it went up to 5 rounds and 43 calls |

**Handbook, for a typical question (about 2 calls)**

| Step | AI call? | What it does |
| --- | --- | --- |
| Search | No | Combines meaning search and keyword search. A ranking model (not a chat AI) picks the best 5 passages in about a second |
| Confidence check | No | If nothing matches well enough, says "I don't know" without calling the AI |
| Write the answer | Yes (1 call) | Writes the answer strictly from those 5 passages |
| Tone check | Yes (1 small call) | Runs at the same time as the answer, and adds a friendly opening when needed |
| Split a two-part question | Sometimes (31% of questions) | Searches each part separately |
| Retry a failed search | Sometimes (9% of questions) | Tries alternative wording once before giving up |

**What this means:**

- **Onyx** spends AI tokens to be flexible and thorough. It reads widely, uses the AI to judge relevance, and searches again when unsure. That buys accuracy on questions that need several documents, at about 13× the tokens and 4× the time.
- **Handbook** does the same steps in code, so it is cheap, fast and predictable. But it gives the AI only 5 short passages, which is too little for "list everything" and big-picture questions.

## Observations

1. **Handbook wins clearly on cost and speed.** It uses about 18× fewer tokens per correct answer, is about 4× faster, and stays steady from question to question.
2. **Onyx wins on accuracy:** by 13 points overall, and by much more on questions that need several documents.
3. **The accuracy gap is not about finding documents.** Both products find the right ones about 80% of the time. The difference is how much each gives the AI to read:
   - **Handbook** passes 5 short passages (6,000 characters at most).
   - **Onyx** reviews about 32 passages, and can read further into the useful ones.
4. **Handbook scored about the same as the basic baseline.** On this question set, its extra steps (reranking, the confidence check, question splitting) didn't add accuracy over plain search plus one AI call. They cost little (about 10% more tokens) but didn't improve results here.
5. **Handbook almost never refuses wrongly.** Its wrong "I don't know" rate was 1%, and it correctly said "I don't know" on every question that truly had no answer.
6. **Onyx has reliability problems under load:**
   - **Its search crashed when the embedding service rate-limited it,** because of a bug in Onyx's error handling. That left 9 test answers written with no documents.
   - **Some of its AI calls ran away** until they hit the model's limit.
   - **Hard questions took up to 28 minutes.**
7. **Our AI reviewer is too strict about "made up".** It flagged real facts as invented. We're fixing its instructions before we report groundedness.

## Limits of this run

- **It's a pilot:** 1,374 documents, not the full 500,000. Search is easier with less noise, so every accuracy figure here is higher than it would be at full scale.
- **We used one AI model, on free services.** The model "thinks" before answering, which inflates response times, and Onyx's more than Handbook's. The token counts for reading are not affected.
- **One AI reviewer graded everything.** A second reviewer checked 10%; no person has checked yet.
- **Some features were off in both products.**
  - **Handbook:** connected tools, the knowledge graph, personal memory and web search were off, because the benchmark is plain files.
  - **Onyx:** web search and deep-research mode were off.

## Recommended next steps

1. **Fix the reviewer and re-review every answer,** so that groundedness ("made up") can be reported reliably.
2. **Give Handbook more to read.** Test 10–20 passages instead of 5 on the separate tuning questions, to see how much of Onyx's accuracy Handbook can match while staying far cheaper.
3. **Repeat the run with a fast, non-thinking model,** to get speed numbers closer to what customers would see.
4. **Run the full benchmark** (all 500 questions and the full document set) once we've acted on the pilot findings.

---

The raw data, the detailed report (`REPORT.md`) and the 10% double-check (`handcheck.csv`) are in `evaluation/reports/bench1/`.
