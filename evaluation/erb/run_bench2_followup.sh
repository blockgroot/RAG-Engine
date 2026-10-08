#!/bin/zsh
# Benchmark 2 follow-up: Handbook-only variants aimed at the gaps Benchmark 2 found,
# on the same model (gpt-oss:120b, low effort, Ollama Cloud) and reviewer (Gemma 4 31B),
# so the results compare directly with evaluation/reports/bench2/.
#
#   A  Benchmark 2's Handbook settings (dev only, the baseline)
#   B  A + wide reads, 40 candidates, partial-answer and newer-document rules, 0.2 cutoff
#   C  B with up-front rephrasing OFF  <- kept: most accurate, fewest AI calls
#   D  C + 2 passages per extra document + an "answer every item" rule on wide reads
#      (no gain; the rule was removed from the code afterwards)
#
# Values come from evaluation.erb.calibrate_wide (reranker scores over all 200 questions).
# Start a proxy on 4003 logging to this folder first (see run_bench2.sh for the command).
set -e
cd "$(dirname "$0")/../.."
export BENCH_DIR=evaluation/reports/bench2-followup
py() { .venv/bin/python -m "$@"; }
base=(--set LLM_BASE_URL=http://localhost:4003/v1 --set QUERY_CACHE_ENABLED=false
      --set RAG_NEIGHBOR_CHUNKS=1 --set RAG_FOCUS_RULE=true)
new=(--set RETRIEVAL_CANDIDATE_POOL=40 --set RAG_WIDE_MAX_HITS=10 --set RAG_WIDE_DOC_RATIO=0.3
     --set RAG_WIDE_PER_DOC=1 --set RAG_PARTIAL_RULE=true --set RAG_CONFLICT_RULE=true
     --set RETRIEVAL_RERANK_MIN_RATIO=0.2)
run() { py evaluation.erb.run_bench --system handbook --runs 1 "$@"; }

run --split dev  --tag A      "${base[@]}" --set RECOVERY_PROACTIVE=true
run --split dev  --tag B      "${base[@]}" "${new[@]}" --set RECOVERY_PROACTIVE=true
run --split dev  --tag C      "${base[@]}" "${new[@]}" --set RECOVERY_PROACTIVE=false
run --split test --tag B-test "${base[@]}" "${new[@]}" --set RECOVERY_PROACTIVE=true
run --split test --tag C-test "${base[@]}" "${new[@]}" --set RECOVERY_PROACTIVE=false
py evaluation.erb.join_tokens
for s in A B C B-test C-test; do
  BENCH_REVIEW_BASE=https://ollama.com/v1 BENCH_REVIEW_MODEL=gemma4:31b \
    py evaluation.erb.review --version v2 --system handbook-$s --key-env OLLAMA_API_KEY
done
