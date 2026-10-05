#!/usr/bin/env bash
# Benchmark 1 answer runs, one system at a time so proxy windows never overlap.
# Resumable: re-running skips every (question, run) already recorded.
set -u
cd "$(dirname "$0")/../.."
run() { .venv/bin/python -m evaluation.erb.run_bench "$@"; }

run --system handbook --split dev  --runs 1
run --system handbook --split test --runs 3
run --system basic    --split dev  --runs 1
run --system basic    --split test --runs 3
run --system onyx     --split dev  --runs 1
run --system onyx     --split test --runs 3
# Context size vs accuracy (Handbook, dev, 1 run). top_k=5 is the run above.
run --system handbook --split dev --runs 1 --tag topk3  --set RAG_TOP_K=3
# The context budget (6,000 chars, sized for 5 chunks) scales with k, or a larger k is cut back to ~5.
run --system handbook --split dev --runs 1 --tag topk10 --set RAG_TOP_K=10 --set RAG_MAX_CONTEXT_CHARS=12000
run --system handbook --split dev --runs 1 --tag topk20 --set RAG_TOP_K=20 --set RETRIEVAL_CANDIDATE_POOL=20 --set RAG_MAX_CONTEXT_CHARS=24000
.venv/bin/python -m evaluation.erb.join_tokens
echo ALL_RUNS_DONE
