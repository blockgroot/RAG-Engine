# Handbook: Benchmarks to Run

As of 2026-09-30

## Summary

We should run eight benchmarks. The first three are the ones we care about most: token use, answer quality, and protection against hidden instructions. The other five are ones competitors and researchers already publish, and buyers will ask about them.

Each benchmark has one main number to optimize. Other numbers are tracked next to it, so we can't improve one by quietly making another worse.

| Benchmark | Question it answers | Main number to optimize | Have today? |
| --- | --- | --- | --- |
| 1. Token use and context | How much do we spend per good answer? | Tokens per correct answer | Partly. We log tokens per call but don't add them up |
| 2. Answer quality | How often do we give a good answer, and how often do we make things up? | Made-up answer rate (target: under 2%) | Partly. 17-case golden set, no head-to-head |
| 3. Hidden instructions | Can a planted document make the bot misbehave or leak data? | Leak rate (target: 0) | Partly. Probe scripts and tests, no public report |
| 4. Finding the right documents | Does search bring back the right documents? | Document recall | Partly. Retrieval eval exists |
| 5. Speed | How long until the first word appears? | Time to first word | No script |
| 6. Permission leaks | Can someone get an answer from a document they aren't allowed to see? | Leaks (must be 0) | Yes, as tests. No report |
| 7. Freshness | How long after an edit can the bot answer with the new version? | Minutes from edit to correct answer | No |
| 8. Charts | Are the numbers in charts exactly right? | Charts that match a hand count (target: 100%) | No |

## How to run a benchmark

The same rules apply to every benchmark below. Most bad benchmark numbers come from breaking one of them.

1. **Fix everything except the thing you're testing.** Same model, same settings, same question set, same data. Write down the model name, the git commit and the date with every result.
2. **Use the same model for every system you compare.** If we answer with Gemini and Onyx answers with GPT, we're comparing models, not products.
3. **Never tune on the questions you report.** Split each question set in half. Tune on one half ("dev") and report on the other ("test"). If we tweak prompts until the test set looks good, the number means nothing.
4. **Run each question 3 times.** AI answers vary. Report the average and the spread, not the best run.
5. **Grade with two AI judges from different companies**, for example one OpenAI model and one Anthropic model, and have a person check 10% of their grades. If the judges disagree often, the grading rules are unclear. Fix the rules, not the numbers.
6. **Keep every raw answer.** Save the question, answer, sources, tokens, time and grade for every run in `evaluation/reports/`, so anyone can re-check a number later.
7. **Report failures next to wins.** Never publish a win rate without the made-up rate and the wrong "I don't know" rate beside it.

## Which data to use

No single dataset covers everything. We need four kinds, and each benchmark uses the ones that fit.

| Data | What it is | Good for | Not good for |
| --- | --- | --- | --- |
| **EnterpriseRAG-Bench** (public) | 500,000 made-up company documents and 500 questions, from Onyx | Answer quality, finding documents, tokens, speed. Also comparing with Onyx and the public leaderboard | Permissions, charts, freshness, our connectors (it's files, not live tools). See the next section |
| **Our test workspace** (we build it) | Real Notion, Slack, Linear, GitHub and Drive accounts filled with a made-up startup's content | Everything that needs live tools: connectors, Slack bot, freshness, charts, per-document sharing, head-to-head against Notion AI and Slack AI | Scale: it will be small |
| **Attack sets** (public and our own) | Documents with hidden instructions planted in them | Hidden instruction benchmark only | Anything else |
| **Our golden set** (exists) | 17 hand-picked cases in `evaluation/golden_set.py` | Catching regressions on every push | Measuring quality. It's too small |

### What goes in each benchmark

| Benchmark | Data to use |
| --- | --- |
| 1. Token use | EnterpriseRAG-Bench (a 50-question sample first, then all 500) plus our test workspace set. Token counts come from the per-call log in `app/llm/metering.py` |
| 2. Answer quality | EnterpriseRAG-Bench for the public number. Our test workspace set (about 200 questions) for our buyer's tools. Add about 100 "the answer isn't here" questions, because EnterpriseRAG-Bench has only 20 (4%), which is too few to measure made-up answers |
| 3. Hidden instructions | Our company-document attack set, [deepset prompt-injections](https://huggingface.co/datasets/deepset/prompt-injections) and NotInject (already in `scripts/bench_injection_guard.py`), plus BIPIA. Also plant the Slack AI and EchoLeak style attacks as documents inside the test workspace, so they go through real ingest |
| 4. Finding documents | EnterpriseRAG-Bench. It lists the right documents for each question, so document recall comes for free |
| 5. Speed | The same runs as 1 and 2. Record the time to first word and the total time for each question |
| 6. Permission leaks | Our test workspace, with a Drive folder and a private Slack channel shared with only some test users. Also EnterpriseRAG-Bench with made-up sharing lists added (see below). That gives a leak test at 500,000-document scale |
| 7. Freshness | Our test workspace only. It needs a real edit in a real tool |
| 8. Charts | Our test workspace: a GitHub repo and a Linear team with activity we have counted by hand |

## EnterpriseRAG-Bench: can we use it?

**Short answer: yes, and we should.** It takes some setup work, and it tests less than half of what we care about. It is our closest competitor's own benchmark, it has a public leaderboard, and scoring well on it is a strong sales story.

### What it is

- **Made by Onyx**, MIT license, released March 2026 (v1.0.0). About 575 stars, and the last update was September 2026 ([GitHub](https://github.com/onyx-dot-app/EnterpriseRAG-Bench), [paper](https://arxiv.org/abs/2605.05253)).
- **Documents:** about 507,000, from a made-up AI company called "Redwood Inference". They come as `.txt` files, and the full zip is 1.26 GB. Slack 285,605, Gmail 121,390, Linear 35,308, Google Drive 25,108, HubSpot 15,017, Fireflies 10,173, GitHub 8,052, Jira 6,120, Confluence 5,189 ([Hugging Face](https://huggingface.co/datasets/onyx-dot-app/EnterpriseRAG-Bench)).
- **Realistic noise:** misfiled documents, outdated facts, and near-copies with changed or conflicting facts. It also uses internal code names and jargon.
- **Questions:** 500 in `questions.jsonl`, in 10 types: basic, reworded, reasoning within one long document, project, constrained, conflicting info, find-everything, informal, high-level, and "info not found". Each question has the right answer, the facts it must contain, and the right document ids. There are 100 more questions in `extra_questions.jsonl` about details like who owns a document or a due date. These fit our provenance line well.
- **Grading:** you give it one line per question, `{"question_id", "answer", "document_ids"}`. It scores correctness, completeness (share of required facts present), document recall, and wrong extra documents. It also has a head-to-head mode that compares two systems using three judges.
- **Leaderboard:** [on Hugging Face](https://huggingface.co/spaces/onyx-dot-app/EnterpriseRAG-Bench-Leaderboard). Onyx leaves itself off. To submit, you email them, and you must provide either a guide to reproduce your result (open source) or an endpoint they can test (closed source).

### How well it fits us

| What we want to test | Does it cover it? |
| --- | --- |
| Answer quality and made-up answers | **Yes**, but only 20 "info not found" questions. Add our own |
| Finding the right documents | **Yes.** Every question lists its right documents |
| Token use and context | **Yes.** Same 500 questions for every system |
| Conflicting documents (a USP candidate) | **Yes**, 20 questions |
| Our connectors | **Partly.** 4 of its 9 sources are tools we support (Slack, Linear, Drive, GitHub). We have no Gmail, HubSpot, Fireflies, Jira or Confluence connector, and it has no Notion. Either way it's files, not live tools, so no connector code runs |
| Tool routing | **Partly.** Our router picks one agent per tool. Documents from tools we don't have need a home |
| Per-document permissions | **No.** Documents carry a `confidentiality` label but no sharing lists. We can add made-up ones |
| Hidden instructions, charts, freshness, Slack bot | **No** |

### What it takes to run

1. **A loader.** Read the `.txt` files and write them straight into our `documents` table. Keep each file's `dsid_...` id as `source_external_id`, so our answers can return the right document ids. Label Slack, Linear, Drive and GitHub documents with our own provider names. Put the other five sources under one test provider.
2. **Two ways to run it.**
   - **Core mode:** call the answer pipeline directly with no tool pinned. This measures search and answering, and is the number to compare with the leaderboard.
   - **Product mode:** go through the router, using only the 4 matching tools. This measures what a real customer gets.
3. **Turn off the per-chunk AI step at ingest.** Each chunk normally gets a short AI-written context line. At about a million chunks, that's a million model calls. The golden-set harness already ingests without it. Also leave the injection scorer off (`GUARD_MODE=off`).
4. **Run on a local Postgres, not Supabase.** A million 1,024-number vectors is about 4 GB before the index. The free Supabase tier is far too small.
5. **Fix one scaling problem first.** The spelling-fix step (`list_chunk_texts` in `app/vectorstore/pgvector_store.py`) loads the text of every chunk for the company into memory. That's fine for a few hundred documents and a few gigabytes here. Switch it off for the run, or cap it. Finding problems like this is a good reason to run the benchmark at all.
6. **A paid judge key.** The grader supports only OpenAI or Anthropic (defaults: `gpt-5.4` or `claude-sonnet-4-6`). The free Gemini key we use for answers won't work. Its cost isn't published, so measure it on the 50-question sample first.
7. **Answer budget.** 500 questions times about 6 model calls is about 3,000 calls per run, and we want 3 runs. On the free Gemini tier (15 per minute) that's more than 3 hours per run, and the daily limit will cut it off. Use Groq or a paid key for the benchmark.

### Time to embed

Embedding a million chunks with local BGE-M3 will take hours on a laptop. We haven't measured it, so time the first 10,000 documents and multiply. The remote embedding backend moves the wait elsewhere, but doesn't remove it.

### Start small

Don't start with all 500,000 documents:

1. **Pilot:** 50 questions, their right documents, and 20,000 random other documents as noise. It proves the loader, the output file and the grader work end to end. Label it clearly as a pilot, because less noise makes search easier than the real thing.
2. **Full run:** all documents and all 500 questions, on a local Postgres. This is the only number we publish.

### Bonus: a permission leak test at scale

In the loader, give each document a made-up sharing list, for example one of five test teams. Then ask each question as a user whose team can't see the right document. Any answer drawn from that document is a leak. Also check that "not shared with you" shows up when it should. No public benchmark tests this, and it's our main USP.

### Verdict

**Viable.** About 2 to 3 days of work: the loader, the runner that writes the answers file, and the scaling fix. The data is free (MIT), the format is simple, and the grader is ready. Its limits are clear: no permissions, no live connectors, and too few "info not found" questions. We cover those with our own test workspace and extra questions.

## 1. Token use and context management

**Main number: tokens per correct answer.** Tokens alone is the wrong target. A bot that answers nothing uses almost no tokens. So we divide total tokens by the number of correct answers.

### What to measure

For each question:

- **Input and output tokens, per step.** One question makes about 6 model calls: rewrite, split into parts, answer, check, web decision, and tone. We need each step separately, so we know which one to cut.
- **Cost per question** in dollars, using each model's price list.
- **Context used vs context sent.** Of the chunks we put in the prompt, how many actually support the answer? This is "context precision". [One 2026 guide](https://www.premai.io/blog/rag-evaluation-metrics-frameworks-testing-2026/) says aim for 0.7 or higher.
- **Context size vs accuracy.** Run the same questions with 3, 5, 10 and 20 chunks. Find the smallest context that keeps accuracy flat.
- **Tokens for "summarise everything" questions.** These read the whole space, up to 120 chunks. Track them apart, or they hide the normal case.

### How to compare with other tools

We can't see how many tokens Glean or Notion AI use. So we compare in three ways:

1. **Against simple setups, on the same model.** (a) Paste all documents into the prompt (long context). (b) Basic search that sends the top 20 chunks. (c) Our full pipeline. This shows what our context work saves.
2. **Against Onyx.** It's open source, so we can host it ourselves, point it at the same model through a logging proxy, and count its tokens exactly.
3. **Against published claims.** Glean says it used 81% fewer tokens than Claude Cowork ($0.58 vs $2.98 per question) ([Glean report](https://www.glean.com/resources/guides/model-context-benchmark-2026), which is behind a demo form, so we could not check the method). We should publish our own number with the method, since theirs has none we can see.

### What we have

`app/llm/metering.py` already logs input and output tokens for every model call, tagged by step. We need a script that runs a question set and adds up those log lines per question.

## 2. Answer quality: win rate and failure rate

**Main number: made-up answer rate.** A made-up answer is the worst outcome, because the person believes it.

### How to grade each answer

Every answer goes into exactly one bucket:

| Bucket | Meaning | Good or bad |
| --- | --- | --- |
| Correct | Answers the question, and every claim is in the sources | Win |
| Honest "I don't know" | The answer really isn't in the documents, and the bot says so | Win |
| Partial | Correct but misses part of the answer | Half win (track apart) |
| Wrong "I don't know" | The answer IS in the documents, but the bot refused | Fail |
| Made up | States something that isn't in the sources | Fail (the worst) |

- **Win rate** = (correct + honest "I don't know") / all questions. This follows your rule: a win is an answer that isn't made up.
- **Failure rate** = (wrong "I don't know" + made up) / all questions.

**One warning.** A bot that always says "I don't know" would never make anything up, so it would get a perfect win rate. So we must always report the **wrong "I don't know" rate** next to the win rate. [RefusalBench](https://aclanthology.org/2026.eacl-long.321.pdf) found that even top models got this balance right less than 50% of the time on multi-document questions. They were either too sure or too careful.

### The question set

- **Use Onyx's public set.** [EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench) has 500 questions over 500,000 made-up company documents, in 10 types. See "EnterpriseRAG-Bench: can we use it?" above for how to run it.
- **Add our own set** built on a Notion + Slack + Linear + GitHub + Drive workspace, because that is our buyer. About 200 questions to start.
- **About 20 to 30% of questions should have no answer** in the documents. Otherwise the "I don't know" part isn't tested.

### Head-to-head win rate

This is how Glean and Onyx publish their results, so buyers will expect it:

- **Glean** used about 280 real questions and 4 human graders. The graders saw two answers side by side without knowing which tool wrote which, with left and right swapped at random, and gave a 5-point preference ([Glean blog](https://www.glean.com/blog/enterprise-search-evaluation-2026)). They report "preferred 1.9 times more than ChatGPT".
- **Onyx** used 99 questions and scored answers on correctness (40%), completeness (30%), handling uncertainty (20%), staying true to sources (5%) and clarity (5%). Two AI judges from different companies graded. They report win rates such as 64% vs ChatGPT Enterprise ([Onyx blog](https://onyx.app/blog/benchmarking-agentic-rag-on-workplace-questions)).

We should do the same: blind, side by side, left and right swapped at random. Use two AI judges from different companies, and have a person check 10% of the grades. Compare against Onyx (self-hosted), Notion AI and Slack AI on our own test workspace. Report our bucket numbers too, since neither Glean nor Onyx reports a made-up rate.

### What we have

A 17-case golden set that runs on every push, a nightly RAGAS score (`evaluation/`), and `scripts/bench_answer_check.py` (60 RAGTruth examples for the answer checker). Missing: a bigger set, the five buckets, and head-to-head runs.

## 3. Protection against hidden instructions

**Main number: leak rate, target 0.** A leak means private data left the system because a document told the bot to send it.

### What to measure

| Number | Meaning |
| --- | --- |
| Leak rate | Of planted attacks that try to send data out (links, images, Slack formatting), how many worked |
| Attack success rate | Of all planted attacks, how many made the bot do what the attack said (wrong answer, fake link, "contact this email") |
| Answers under attack | How often the bot still answers correctly when an attack document is in the results |
| False alarm rate | How many normal documents get flagged as attacks |

"Attack success rate" and "answers under attack" are the standard numbers in research sets like [AgentDojo](https://www.researchgate.net/publication/397198170_AgentDojo_A_Dynamic_Environment_to_Evaluate_Prompt_Injection_Attacks_and_Defenses_for_LLM_Agents) and [InjecAgent](https://arxiv.org/html/2510.05244v1). Using the same names makes our numbers easy to compare.

### How to prove it, not just claim it

1. **Prove the leak path is closed, in code.** Our link rule removes any link the source text didn't contain. That can be proven with a test that doesn't depend on the model at all: pretend the model is fully taken over, have it write every leak format we know, and check that nothing gets out. `tests/test_exfil_channels.py` already does this. Publish it as "even a fully fooled model can't send data out through a link".
2. **Replay the real attacks.** Rebuild the [Slack AI leak](https://promptarmor.substack.com/p/slack-ai-data-exfiltration-from-private) and [EchoLeak](https://thehackernews.com/2025/06/zero-click-ai-vulnerability-exposes.html) as test documents, run them against us, and publish the result. Buyers know these by name.
3. **Measure attack success on public sets.** [BIPIA](https://arxiv.org/html/2510.05244v1) fits us best, because the attack is hidden in a document, like ours. Add our company-document set, deepset and NotInject (already in `scripts/bench_injection_guard.py`). AgentDojo matters later, when we add actions.
4. **Run each attack many times.** AI answers vary, so one pass proves little. `scripts/probe_injection.py` already does multi-run pass rates.
5. **Red team by hand every quarter.** Automated filters can be beaten by attackers who adjust to them, so fixed test sets alone will look better than reality.
6. **Later, pay an outside firm** for a penetration test, so the claim doesn't rest only on our own tests.

**Testing competitors.** Only on our own accounts: Onyx self-hosted, and a Slack workspace we own. Never against other companies' data. If we find a real hole, report it privately to the vendor before we publish anything.

**Today's gap.** The AI filter (`GUARD_MODE`) is off in production. Until it's on, publish only the leak-rate numbers. Those come from the link rule, which is always on.

## 4. Finding the right documents

**Main number: document recall** (how many of the right documents search found).

EnterpriseRAG-Bench also counts **wrong extra documents**, the irrelevant ones that got pulled in. Its results show plain keyword search beat vector search (68.8% vs 51.4% correct answers) ([paper](https://arxiv.org/html/2605.05253v2)). We use both together, so we should show what that gains us. `evaluation/retrieval_eval.py` and `scripts/compare_retrieval.py` already exist.

## 5. Speed

**Main number: time to first word.** Onyx publishes average total answer time: 34.7 seconds for Onyx, 36.2 for Claude Enterprise, 45.4 for ChatGPT Enterprise and 46.7 for Notion AI ([Onyx blog](https://onyx.app/blog/benchmarking-agentic-rag-on-workplace-questions)). We should report time to first word, because our chat types out the answer at a fixed speed, so the total is longer than the real wait. Report total time too, so our numbers can be compared with theirs.

## 6. Permission leaks

**Main number: leaks, must be 0.** Ask questions whose only answer is in a restricted document, as a user who can't see it. Any answer from that document is a leak. [Others run this](https://tianpan.co/blog/2026/05/04/permission-aware-retrieval-enterprise-rag-access-control) as a test that blocks the build on a single leak.

Also measure how often the "not shared with you" message is right. It should appear when a restricted document matches, and never when it doesn't. This is our main selling point, so it needs its own number. We already have `tests/test_isolation.py` and `tests/test_doc_access.py`. What's missing is a report we can show buyers.

## 7. Freshness

**Main number: minutes from an edit to a correct answer.** Change a fact in Notion, Slack or Drive, then ask about it every few minutes until the bot gives the new answer. We sync every hour, so today's worst case is about an hour. Tools that read live, like Dashworks, will claim zero, so we should know our real number before a buyer asks.

## 8. Charts

**Main number: charts that exactly match a hand count, target 100%.** Count commits, pull requests and tasks by hand for a test workspace, and compare with our charts. Run the same questions on tools where the AI writes the query (Dust, Copilot in Excel). Microsoft itself [warns](https://www.ghacks.net/2025/08/26/copilot-launches-in-excel-but-microsoft-warns-against-using-it-for-any-task-requiring-accuracy/) against using Copilot in Excel for tasks that need accuracy. This benchmark turns "our numbers come from the database" into a number.

## Also worth tracking (smaller)

- **Same answer every time.** Ask each question 5 times and see whether the answers agree. Glean users [complain](https://www.thunai.ai/blog/glean-review) that the same question gives different answers.
- **Sources that back the claim.** Check that the cited document really says what the answer says.
- **Conflicting documents.** When two documents disagree, does the answer say so? EnterpriseRAG-Bench has a set for this.

## What we have and what is missing

| Benchmark | Already in the codebase | Missing |
| --- | --- | --- |
| Token use | Per-call token log in `app/llm/metering.py` | Script that adds it up per question, Onyx logging proxy, simple setups to compare |
| Answer quality | `evaluation/` golden set (17), RAGAS nightly, `scripts/bench_answer_check.py` | 200+ question set, five-bucket grader, EnterpriseRAG-Bench loader and runner, head-to-head |
| Hidden instructions | `scripts/probe_injection.py`, `scripts/bench_injection_guard.py`, `scripts/e2e_injection_guard.py`, `tests/test_exfil_channels.py` | BIPIA run, Slack AI and EchoLeak replays, public report, outside test |
| Finding documents | `evaluation/retrieval_eval.py`, `scripts/compare_retrieval.py` | Run on EnterpriseRAG-Bench |
| Speed | None | Time-to-first-word script |
| Permission leaks | `tests/test_isolation.py`, `tests/test_doc_access.py` | A report for buyers, a live Drive test (never run on a real account), and made-up sharing lists on EnterpriseRAG-Bench |
| Freshness | None | Edit-and-ask script |
| Charts | None | Hand-counted test workspace |

## Order of work

1. **Answer quality buckets on our own set.** It's the base for everything else, and it tells us our made-up rate.
2. **Token script.** Cheap, because the logging already exists. Run it on the same set as step 1 to get tokens per correct answer.
3. **Leak proof and attack replays.** Mostly written already, and it's our strongest story.
4. **EnterpriseRAG-Bench.** Pilot of 50 questions first, then the full run. Build the test workspace at the same time, since benchmarks 6 to 8 need it.
5. **Head-to-head against Onyx, Notion AI and Slack AI.**
6. **Speed, permission-leak report, freshness, charts.**

## Sources

- [Glean evaluation method, 2026](https://www.glean.com/blog/enterprise-search-evaluation-2026)
- [Glean context benchmark report](https://www.glean.com/resources/guides/model-context-benchmark-2026)
- [Onyx: benchmarking agentic RAG on workplace questions](https://onyx.app/blog/benchmarking-agentic-rag-on-workplace-questions)
- [EnterpriseRAG-Bench paper](https://arxiv.org/html/2605.05253v2), [code](https://github.com/onyx-dot-app/EnterpriseRAG-Bench), [dataset](https://huggingface.co/datasets/onyx-dot-app/EnterpriseRAG-Bench) and [leaderboard](https://huggingface.co/spaces/onyx-dot-app/EnterpriseRAG-Bench-Leaderboard)
- [deepset prompt-injections dataset](https://huggingface.co/datasets/deepset/prompt-injections)
- [RefusalBench](https://aclanthology.org/2026.eacl-long.321.pdf)
- [RAG evaluation metrics 2026 (Prem AI)](https://www.premai.io/blog/rag-evaluation-metrics-frameworks-testing-2026/)
- [AgentDojo](https://www.researchgate.net/publication/397198170_AgentDojo_A_Dynamic_Environment_to_Evaluate_Prompt_Injection_Attacks_and_Defenses_for_LLM_Agents)
- [Indirect prompt injection benchmarks compared](https://arxiv.org/html/2510.05244v1)
- [Permission-aware retrieval testing](https://tianpan.co/blog/2026/05/04/permission-aware-retrieval-enterprise-rag-access-control)
- [Slack AI data leak (PromptArmor)](https://promptarmor.substack.com/p/slack-ai-data-exfiltration-from-private)
- [EchoLeak (The Hacker News)](https://thehackernews.com/2025/06/zero-click-ai-vulnerability-exposes.html)
- [Microsoft warns about Copilot in Excel (gHacks)](https://www.ghacks.net/2025/08/26/copilot-launches-in-excel-but-microsoft-warns-against-using-it-for-any-task-requiring-accuracy/)
- [Glean review (Thunai)](https://www.thunai.ai/blog/glean-review)
