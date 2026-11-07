# Encrypted vs Unencrypted Search: Benchmark Results

2 Nov 2026 · branch `bench/encrypted-search` · code in `evaluation/encbench/`

**Summary:** On the same data, the same vectors and the same 200 questions,
storing text **encrypted** with per-company keys, and finding keyword matches
through **secret word codes**, kept accuracy **the same** (97.6% of questions
found the right document, both ways). With the final design, search was **12%
faster** (90 ms vs 102 ms median), the database handled **44% more searches
per second**, and storage was **11% smaller**. Decrypting costs about **1 ms
per question**. In a full answer with Claude Haiku, the two were within the
model's own variation (median 1.75 s vs 1.87 s).

---

## 1. What was compared

| | **A: without encryption** (today) | **B: with encryption** |
| --- | --- | --- |
| Document text | Readable | Encrypted (AES-256-GCM), one key per company |
| Keyword search | Postgres word index on the text (`content_tsv`) | Secret word codes (keyed hash of each word, stored as numbers) |
| Search by meaning | Vector index | Same vector index, same vectors |
| Ranking after search | BM25 on the text, merge, as today | Same, on the decrypted text |
| Table layout | One partition per company, row-level security | The same |

How keyword search works in B:

1. **Saving:** our server splits the text into words, cleans them exactly the
   way Postgres does (lowercase, common words dropped, word endings
   simplified), and turns each word into a code with the company's key.
   `invoice 4521 paid` → three numbers. The text is encrypted; the codes are
   stored next to it.
2. **Asking:** the question's words go through the same steps, giving the same
   codes. The database finds chunks that share codes, ranks them by how many
   they share, and returns at most 200. It never sees a real word.
3. **Our server** decrypts only those chunks and ranks them with BM25 on the
   real text, as today.

---

## 2. How the test was run

| | |
| --- | --- |
| Documents | Benchmark 2 corpus: 1,374 real documents (Slack, Google Drive, Linear, wiki and tickets), 11,060 chunks |
| Companies | 10 copies of the corpus, each with its own key: **110,600 chunks** per version |
| Vectors | Embedded once with BGE-M3 on this machine; A and B load the same vectors |
| Questions | All **200** Benchmark 2 questions. 170 name the right document(s) and are scored for accuracy; 30 (answer not in the documents, or broad overviews) count for speed and agreement only |
| Machine | Apple M2, 8 cores, 16 GB RAM; Postgres 17 + pgvector, locally. Test client and database share the machine |
| AI model | Claude Haiku (`claude-haiku-5-5`), only for the end-to-end test |
| Key service | Simulated: keys kept in memory for 5 minutes; a "key not in memory" fetch costs 0 ms or 100 ms (a KMS round trip from outside AWS) |

**What is timed:** search alone stops when the passages for the AI are ready,
because the AI's own time is the same for A and B and varies far more than
the difference being measured. The end-to-end test then measures full
answers.

Each speed test used 50 questions, picked across question types and sources,
run 10 times, alternating A and B, after a warm-up.

---

## 3. Results

### 3.1 Accuracy

| | A | B |
| --- | --- | --- |
| Right document among the results (170 questions) | **97.6%** | **97.6%** |
| Share of right documents found (questions often have several) | 91.6% | 91.8% |
| Keyword search alone: share of right documents found | 96.6% | 96.5% |
| Meaning search alone: share of right documents found | 93.3% | 93.3% |
| Same first document, A vs B | 86% of questions | |

Only **2 of 170** questions changed: one found by A only, one by B only.
Every question type scored the same or better on B:

| Question type | Questions | A | B |
| --- | --- | --- | --- |
| Basic | 30 | 96.7% | 96.7% |
| Completeness (several documents needed) | 20 | 95% (65% of docs) | 95% (66% of docs) |
| Conflicting information | 20 | 100% | 100% |
| Constrained | 20 | 100% | 100% |
| Reasoning within a document | 20 | 100% | 100% |
| Miscellaneous | 20 | 100% | 100% |
| Project-related | 20 | 100% (78% of docs) | 100% (80% of docs) |
| Semantic | 20 | 90% | 90% |

The keyword candidates differ in order (the database ranks by shared codes
instead of Postgres's word weights), but BM25 on the real text re-ranks them
the same way, so the final results match.

### 3.2 Search speed, one person asking

| | A | B | Difference |
| --- | --- | --- | --- |
| Median | 102 ms | **90 ms** | −12% |
| p95 | 134 ms | **94 ms** | −30% |
| p99 | 145 ms | 120 ms | −17% |

Where the time goes (each step run alone, median):

| Step | A | B |
| --- | --- | --- |
| Meaning search | 76 ms | 75 ms |
| Keyword search | 73 ms | **38 ms** |
| …of which making the question's codes | — | 0.2 ms |
| …of which decrypting the results | — | 1.2 ms |

The two searches run in parallel, so a question takes about as long as the
slower one plus merging. B's keyword search is faster because counting shared
codes is cheaper for the database than today's text-weight ranking (`ts_rank`).

### 3.3 When the key is not in memory

| | Search time, median |
| --- | --- |
| B, key in memory (normal case) | 90 ms |
| B, key re-derived, no KMS delay | 76 ms |
| B, key fetched from KMS (simulated 100 ms) | **180 ms** |

A key fetch adds its round trip once per company every few minutes (160 fetches
in the whole run). From Render to AWS that is about 50–150 ms; on AWS, 10–20 ms.

### 3.4 Many people at once (10 companies, random questions)

| People asking at once | A: searches/s | A: p95 | B: searches/s | B: p95 |
| --- | --- | --- | --- | --- |
| 1 | 9.5 | 157 ms | 10.4 | 134 ms |
| 5 | 15.4 | 457 ms | **20.8** | 319 ms |
| 10 | 14.7 | 856 ms | **21.5** | 584 ms |
| 25 | 14.7 | 1.89 s | **21.7** | 1.30 s |
| 50 | 14.7 | 3.54 s | **21.1** | 2.55 s |

No errors in either. A tops out at about 15 searches per second on this
laptop, B at about 21 (+44%). Above that, both queue: times rise with the
number of people, not because anything fails. The pool has 10 database
connections, as the app does by default.

The sizing plan (`docs/pgvector-sizing-final.md`) assumes 10 searches per second
at peak; both versions meet it here, on a laptop that also runs the test.

### 3.5 Full answers with Claude Haiku (20 questions, each on A and B)

| | A | B |
| --- | --- | --- |
| Median | 1.87 s | 1.75 s |
| Average | 2.13 s | 2.19 s |
| p95 | 4.17 s | 4.14 s |

The difference is within the AI's own variation: the same question can take
1.5 s on one call and 2.3 s on the next. Search is about 0.1 s of a 2 s answer.

### 3.6 Saving documents and storage

| | A | B |
| --- | --- | --- |
| Saving a chunk (database, incl. both indexes) | 11.8 ms | 11.7 ms |
| Encrypting + making codes, per chunk | — | **0.12 ms** |
| Codes per chunk (distinct words) | — | 71 |
| Table data | 504 MB | 449 MB |
| Vector index | 301 MB | 301 MB |
| Keyword index | 77 MB (word index) | **38 MB** (code index) |
| **Total** | **890 MB** | **795 MB (−11%)** |

B is smaller because it stores no word list (`content_tsv`) and its codes are
4-byte numbers. Encryption adds 28 bytes per chunk.

The spelling corrector reads every chunk of a company once per server process:
63 ms on A, 56 ms on B (decrypting 11,060 chunks).

---

## 4. What we learned on the way

The first version stored codes as text and ranked matches by intersecting text
arrays. It was accurate but slow:

| | A | B, first version | B, final |
| --- | --- | --- | --- |
| Search, one person, median | 102 ms | 282 ms | **90 ms** |
| Keyword search step | 73 ms | 245 ms | **38 ms** |
| Most searches per second | ~15 | ~7 | **~21** |
| Full answer with Haiku, median | 2.0 s | 2.5 s | 1.75 s |
| Storage | 890 MB | 930 MB | 795 MB |

Decryption was never the cost (about 1 ms throughout). The time went into
ranking: a typical question's words match about 7,500 of a company's 11,060
chunks, and the database ranks all of them before keeping 200. Counting shared
codes on whole numbers with Postgres's `intarray` extension takes 6.8 ms for
that step, against 38 ms for today's `ts_rank` and 102 ms for the text-array
version, and returned the same top 200 on all 40 questions checked.

The codes are the first 4 bytes of each word's keyed hash. Two different
words of one company share a code about once in 4 billion pairs; that would at
most add an extra candidate, which BM25 then ranks on the real text.

---

## 5. Limits of this test

- **One laptop.** The database and the test client share 8 cores, so
  absolute numbers are lower than a database server would give. The comparison
  between A and B is the result, not the absolute speed.
- **Same vectors, local model.** The Benchmark 2 vectors were Jina's; both
  versions here use BGE-M3. That affects both equally.
- **No reranker.** The local reranker is not installed here. It would run on the
  same passages for A and B.
- **Simulated KMS.** Real AWS KMS latency depends on where the server runs
  (section 3.3).
- **Only `chunks`.** Document titles, chats, answers, the knowledge graph and
  memory also hold customer text and would need the same treatment.
- **Charts not tested.** The plan for them is in `docs/encrypted-search-plan.md`.
- **What the database can still learn:** that two chunks share a word, and
  roughly how common each code is, never which word it is. Vectors stay
  unencrypted.

---

## 6. Conclusion

Encrypting chunk text and searching keywords through per-company word codes
does **not** cost accuracy, and with codes stored as numbers it makes search
**faster and smaller** than today, not slower. The remaining cost is a key
fetch every few minutes per company (section 3.3).

What is still needed before building it into Handbook: moving the backend to
AWS (so "only our server can decrypt" holds), extending encryption to every
table that holds customer text, and the chart plan.

## 7. How to run it again

```
pip install snowballstemmer             # the bench's word stemmer (not an app dependency)
python -m evaluation.encbench.embed     # once: local vectors (~25 min)
python -m evaluation.encbench.build     # both versions, 10 companies (~45 min)
python -m evaluation.encbench.run       # accuracy, speed, many users (~15 min)
LLM_ADAPTER=anthropic LLM_MODEL=claude-haiku-5-5 LLM_API_KEY=... \
  python -m evaluation.encbench.run_llm # full answers (~5 min)
```

Raw results: `evaluation/reports/encbench/` (`results.json`, `results_llm.json`,
`build.json`, and the first version's `*_text_codes.json`).
