#!/usr/bin/env bash
# Benchmark 1, end to end: answers (one system at a time, so proxy windows never
# overlap), token join, one review call per answer, report.
# Resumable at every step: re-running skips whatever is already recorded.
# Needs the proxy running (python -m evaluation.erb.proxy) and Onyx up.
set -eu
cd "$(dirname "$0")/../.."
py() { .venv/bin/python -m "$@"; }

# All 200 questions once; runs 2-3 only for the 50 repeat questions.
py evaluation.erb.run_bench --system handbook --split all --runs 3
py evaluation.erb.run_bench --system onyx     --split all --runs 3
py evaluation.erb.join_tokens
py evaluation.erb.review
py evaluation.erb.report > /dev/null
echo ALL_DONE
