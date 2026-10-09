# Benchmarks

Plans and results for Handbook's benchmarks, kept apart from the product docs.

| File | What it is |
| --- | --- |
| [benchmarks.md](benchmarks.md) | The eight benchmarks we plan to run, and the rules every run follows |
| [benchmark-1-plan.md](benchmark-1-plan.md) | The plan for Benchmark 1: token use and context management, Handbook vs Onyx |
| [benchmark-1-results.md](benchmark-1-results.md) | Benchmark 1 results in plain English: the summary to share, including the follow-up on giving Handbook more to read |
| [benchmark-2-results.md](benchmark-2-results.md) | Benchmark 2: the same test with a different AI model (gpt-oss-120b) and Handbook's accuracy settings on; includes Onyx never saying "I don't know" |
| [benchmark-2-followup.md](benchmark-2-followup.md) | Follow-up: Handbook changes aimed at the Benchmark 2 gaps, tested on the same 150 questions; the kept settings and what is still open |

The scripts are in `evaluation/erb/`. The raw run data is in `evaluation/reports/bench1/` (with the detailed generated `REPORT.md`), `evaluation/reports/bench2/` and `evaluation/reports/bench2-followup/`.
