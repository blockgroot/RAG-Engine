#!/bin/zsh
# Benchmark 2: the same 150 test questions as Benchmark 1, asked ONCE (no repeats),
# with gpt-oss:120b (OpenAI's open model, reasoning effort "low") on Ollama Cloud as
# the answer model for every system, and Handbook with the accuracy settings
# Benchmark 1 led to. The weak-passage cutoff stays off; its scores are logged
# (raw.rerank_scores) so this run can calibrate it. gpt-oss cannot switch thinking
# off entirely; "low" keeps it to ~15-30 tokens a call, and it is counted.
#
# Start the proxy first, pointed at Ollama Cloud and logging to bench2:
#   BENCH_UPSTREAM_BASE=https://ollama.com/v1 BENCH_UPSTREAM_KEY=$OLLAMA_API_KEY \
#   BENCH_MODEL=gpt-oss:120b BENCH_REASONING_EFFORT=low \
#   BENCH_PROXY_LOG=evaluation/reports/bench2/proxy.jsonl .venv/bin/python -m evaluation.erb.proxy
#
# Onyx runs first: it is ~90% of the tokens, and the free plan has a monthly allowance.
# Grading runs afterwards with Gemma 4 31B (Google), a different company:
#   BENCH_DIR=evaluation/reports/bench2 BENCH_REVIEW_BASE=https://ollama.com/v1 \
#   BENCH_REVIEW_MODEL=gemma4:31b .venv/bin/python -m evaluation.erb.review --version v2 --key-env OLLAMA_API_KEY
set -e
cd "$(dirname "$0")/../.."
export BENCH_DIR=evaluation/reports/bench2
py() { .venv/bin/python -m "$@"; }
handbook=(--set RAG_NEIGHBOR_CHUNKS=1 --set RAG_FOCUS_RULE=true --set RECOVERY_PROACTIVE=true)

ask_all() {
  # An answer cached by an earlier configuration must not be served as this one's.
  psql -d bench1 -qc "DELETE FROM query_answer_cache"
  py evaluation.erb.run_bench --system onyx     --split test --runs 1
  py evaluation.erb.run_bench --system handbook --split test --runs 1 "${handbook[@]}"
  py evaluation.erb.run_bench --system basic    --split test --runs 1
}

ask_all
py evaluation.erb.requeue   # answers spoiled by provider hangs or Jina rate limits
ask_all                     # second pass: only the requeued and errored questions
py evaluation.erb.join_tokens
