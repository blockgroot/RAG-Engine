#!/bin/zsh
# Benchmark 2: the same 150 test questions as Benchmark 1, asked ONCE (no repeats),
# with Gemini 3.6 Flash (thinking off) as the answer model for every system, and
# Handbook with the accuracy settings Benchmark 1 led to. The weak-passage cutoff
# stays off; its scores are logged (raw.rerank_scores) so this run can calibrate it.
#
# Start the proxy first, pointed at Gemini and logging to bench2. Several keys from
# separate Google projects are used in turn (free tier: 5 requests/min per project):
#   BENCH_UPSTREAM_BASE=https://generativelanguage.googleapis.com/v1beta/openai \
#   BENCH_UPSTREAM_KEY="$GEMINI_API_KEY,$GEMINI_API_KEY_3,$GEMINI_API_KEY_OLD" \
#   BENCH_MODEL=gemini-3.6-flash BENCH_REASONING_EFFORT=none \
#   BENCH_PROXY_LOG=evaluation/reports/bench2/proxy.jsonl .venv/bin/python -m evaluation.erb.proxy
#
# Grading runs separately, afterwards, with a reviewer from another company.
set -e
cd "$(dirname "$0")/../.."
export BENCH_DIR=evaluation/reports/bench2
py() { .venv/bin/python -m "$@"; }
handbook=(--set RAG_NEIGHBOR_CHUNKS=1 --set RAG_FOCUS_RULE=true --set RECOVERY_PROACTIVE=true)

ask_all() {
  # An answer cached by an earlier configuration must not be served as this one's.
  psql -d bench1 -qc "DELETE FROM query_answer_cache"
  py evaluation.erb.run_bench --system handbook --split test --runs 1 "${handbook[@]}"
  py evaluation.erb.run_bench --system basic    --split test --runs 1
  py evaluation.erb.run_bench --system onyx     --split test --runs 1
}

ask_all
py evaluation.erb.requeue   # answers spoiled by provider hangs or Jina rate limits
ask_all                     # second pass: only the requeued and errored questions
py evaluation.erb.join_tokens
