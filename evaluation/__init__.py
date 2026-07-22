"""Golden-set evaluation harness for the Policy Agent (Phase 7).

A small, deliberate regression suite — not a benchmark — run two ways:

- **path-firing** (deterministic, cheap): did the right path fire for each golden
  case (policy / fallback / web), and are the expected facts present. This is what
  runs on every push and what ``tests/test_golden_set.py`` asserts.
- **RAGAS** (LLM-judged, expensive): faithfulness / answer relevancy / context
  precision / recall on the answerable cases. Optional dependency; runs less often.

Entry point: ``python -m evaluation.run_eval`` (see evaluation/README.md).
"""
