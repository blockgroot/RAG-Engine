"""Plaintext vs encrypted search, on the same data (docs/benchmarks/encrypted-search-comparison.md)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "evaluation" / "reports" / "encbench"
QUESTIONS = ROOT / "evaluation" / "reports" / "bench2" / "pilot_questions.jsonl"
SOURCE_DB = "postgresql://localhost/bench1"
SOURCE_ORG = "bench1-erb-pilot"
BENCH_DB = "postgresql://localhost/enc_bench"
TENANTS = 10
