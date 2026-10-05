"""Build the frozen Benchmark 1 question set and corpus from EnterpriseRAG-Bench (plan §5).

200 questions: 20 of every type, except ``high_level`` (the dataset has only
10) whose shortfall is taken from ``basic`` (175 available). Every gold
document of those questions is in the corpus, plus a fixed set of random
noise documents — one seed, one manifest, never edited afterwards. Every
system loads exactly these documents.

``--keep`` carries an earlier manifest's questions and noise forward, so the
documents already embedded are reused instead of paid for again.

Splits: 50 dev / 150 test, stratified by type. ``repeat`` marks 50 test
questions (5 per type) that are answered 3 times to measure run-to-run
variation; every other question is answered once (benchmarks.md rule 4 on a
subset — three runs of all 150 would triple the judging cost).

Run: .venv/bin/python -m evaluation.erb.build_pilot --data ~/Desktop/bench1-data [--keep old_manifest.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import zipfile
from collections import defaultdict
from pathlib import Path

SEED = 20261005
PER_TYPE = 20
TOP_UP_TYPE = "basic"  # absorbs the high_level shortfall
TOTAL = 200
DEV_TOTAL = 50
REPEAT_PER_TYPE = 5
_DSID = re.compile(r"(dsid_[0-9a-f]{32})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--noise", type=int, default=1000)
    ap.add_argument("--keep", type=Path, default=None, help="earlier manifest whose questions and noise are reused")
    ap.add_argument("--out", type=Path, default=Path("evaluation/reports/bench1/manifest.json"))
    args = ap.parse_args()
    data = args.data.expanduser()
    rng = random.Random(SEED)
    kept = json.loads(args.keep.read_text()) if args.keep else {"questions": [], "noise_doc_ids": []}
    kept_ids = {q["question_id"] for q in kept["questions"]}

    questions = [json.loads(line) for line in (data / "questions.jsonl").open()]
    by_type: dict[str, list[dict]] = defaultdict(list)
    for q in sorted(questions, key=lambda q: q["question_id"]):
        by_type[q["question_type"]].append(q)
    quota = {t: min(PER_TYPE, len(qs)) for t, qs in by_type.items()}
    quota[TOP_UP_TYPE] += TOTAL - sum(quota.values())

    picked: list[dict] = []
    for qtype in sorted(by_type):
        have = [q for q in by_type[qtype] if q["question_id"] in kept_ids]
        rest = [q for q in by_type[qtype] if q["question_id"] not in kept_ids]
        picked += have + rng.sample(rest, quota[qtype] - len(have))

    # Dev quota per type: a quarter, rounded down, then topped up from the
    # largest types until the dev split totals DEV_TOTAL.
    dev_quota = {t: quota[t] // 4 for t in quota}
    for t in sorted(quota, key=lambda t: -quota[t]):
        if sum(dev_quota.values()) >= DEV_TOTAL:
            break
        dev_quota[t] += 1
    split, repeat = {}, set()
    for qtype in sorted(by_type):
        group = [q for q in picked if q["question_type"] == qtype]
        rng.shuffle(group)
        for i, q in enumerate(group):
            split[q["question_id"]] = "dev" if i < dev_quota[qtype] else "test"
        test = [q for q in group if split[q["question_id"]] == "test"]
        repeat |= {q["question_id"] for q in test[:REPEAT_PER_TYPE]}

    zf = zipfile.ZipFile(data / "all_documents.zip")
    path_of = {}
    for name in zf.namelist():
        m = _DSID.search(name)
        if m and name.endswith(".txt"):
            path_of[m.group(1)] = name

    gold = sorted({d for q in picked for d in q["expected_doc_ids"]})
    missing = [d for d in gold if d not in path_of]
    if missing:
        raise SystemExit(f"gold documents not in the archive: {missing[:5]} ({len(missing)})")
    noise = sorted(set(kept["noise_doc_ids"]) - set(gold))
    pool = sorted(set(path_of) - set(gold) - set(noise))
    noise += sorted(rng.sample(pool, max(0, args.noise - len(noise))))
    noise.sort()

    docs_dir = data / "pilot_docs"
    docs_dir.mkdir(exist_ok=True)
    chars = 0
    for dsid in gold + noise:
        text = zf.read(path_of[dsid]).decode("utf-8", errors="replace")
        chars += len(text)
        (docs_dir / f"{dsid}.txt").write_text(text)

    doc_ids = gold + noise
    manifest = {
        "seed": SEED,
        "dataset": "onyx-dot-app/EnterpriseRAG-Bench v1.0.0",
        "questions": [
            {"question_id": q["question_id"], "question_type": q["question_type"],
             "split": split[q["question_id"]], "repeat": q["question_id"] in repeat}
            for q in picked
        ],
        "gold_doc_ids": gold,
        "noise_doc_ids": noise,
        "doc_paths": {d: path_of[d] for d in doc_ids},
        "total_chars": chars,
        "sha256": hashlib.sha256("\n".join(doc_ids).encode()).hexdigest(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=1))
    (args.out.parent / "pilot_questions.jsonl").write_text(
        "".join(json.dumps(q) + "\n" for q in sorted(picked, key=lambda q: q["question_id"])))
    n_dev = sum(1 for v in split.values() if v == "dev")
    print(f"questions={len(picked)} (dev {n_dev} / test {len(picked) - n_dev}, repeat {len(repeat)}) "
          f"gold={len(gold)} noise={len(noise)} docs={len(doc_ids)} chars={chars:,}")


if __name__ == "__main__":
    main()
