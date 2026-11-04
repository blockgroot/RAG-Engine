# Load test: 10 tenants, measured

Run on 29 Oct 2026 against a local Postgres 17 + pgvector 0.8 database, with the
real outside services: gpt-oss:120b on Ollama for the AI, Jina v3 for embeddings
and reranking. It measures the inputs that `pgvector Sizing and Cost for 10 Tenants` assumed.

Status: **complete**: ingest of all 10 tenants, and 100 real questions, all
answered. The concurrent-user load test is next.

## 1. What was loaded

10 tenants, each a separate organization with 10 public RFC documents of 20–30
pages, grouped by IETF area.

| | Total | Per tenant (average) | Per document (average) |
|---|---|---|---|
| Documents | 100 | 10 | 1 |
| Pages | 2,428 | 243 | 24 |
| Words | 737,341 | 73,700 | 7,370 |
| Extracted text | 5.8 MB | 0.58 MB | 58 KB |

Ingest followed the production order: read the document, chunk and embed it,
read its figures for charts, build the graph, then contextual enrichment (a short
summary added to each chunk, which is then embedded again).

## 2. Chunks and vectors

| | Doc assumed | Measured |
|---|---|---|
| Chunk size | 512 tokens, 15% overlap | **256 tokens, 40 overlap** (setting); stored chunks average **~245 tokens** with their summary |
| Vectors per 1,000 words | ~3 | **11.8** |
| Vectors per tenant (this run) | – | 782 – 1,024, average 868 |
| Vectors, all 10 tenants (this run) | – | 8,682 |
| Embedding | 1536 dims, halfvec (2 bytes) | **1024 dims, full `vector` (4 bytes)** |
| One vector | 3.1 KB | **4.1 KB** (measured column size) |

Chunks are smaller than the doc assumed, neighbouring chunks overlap, and every
chunk also carries a summary of its document. That gives about 4× more vectors
for the same words.

## 3. Storage, measured

For the 8,682 vectors in the database:

| | Size | Per vector | Doc's estimate per vector |
|---|---|---|---|
| HNSW index (`idx_chunks_embedding`) | 69 MB | **8.4 KB** | ~3.3 KB |
| Chunks table: text + vector, without indexes | 67 MB | **7.8 KB** | ~5.4 KB |
| Whole database (all tables, graph, chart tables) | 149 MB | – | – |

The index was grown row by row during ingest, not built in one pass. A one-pass
rebuild (REINDEX) may be smaller; this is not yet measured.

### Projected to the doc's workload (10 tenants × 30M words)

Measured ratios applied to 300M words:

| | Doc | Projected from this run |
|---|---|---|
| Vectors | 0.92M | **~3.5M** |
| HNSW index | 3.1 GB | **~30 GB** |
| Table | 4.8 GB | **~28 GB** |
| Hot set (index + 30% of table) | ~4.6 GB | **~38 GB** |
| Fits a 32 GB db.r7g.xlarge? | yes | **no**: 64 GB db.r7g.2xlarge, or halfvec / a rebuilt index first |

RFCs are plain technical text. A company's real documents may chunk
differently, so treat the projection as the order of magnitude.

## 4. AI and embedding usage to ingest

Provider-reported tokens, all 10 tenants, including the re-reads after hangs:

| Step | Service | Calls | Input tokens | Cached input | Output tokens | Avg seconds |
|---|---|---|---|---|---|---|
| Figure reader (numbers in prose, for charts) | gpt-oss | 493 | 1.73M | 0.32M | 0.87M | 5.5 |
| Contextual enrichment (chunk summaries) | gpt-oss | 892 | 2.30M | 1.85M (80%) | 0.28M | 1.9 |
| Identifier-masking note | gpt-oss | 35 | 0.10M | 0.04M | 0.05M | 4.0 |
| **AI total** | gpt-oss | **1,420** | **4.13M** | **2.20M (53%)** | **1.21M** | |
| Embeddings (ingest, then again after enrichment) | Jina | 1,180 | 4.14M | – | – | 0.9 |

Per tenant (10 documents, ~74k words), the average is:

- **AI:** 142 calls, 413k input tokens (220k of them cached) and 121k output tokens.
- **Jina:** 118 calls and 414k tokens.

Per 1,000 words of documents:

- **AI:** about 5,600 input tokens and 1,630 output tokens.
- **Jina:** about 5,600 tokens.

The figure reader produces **72% of all AI output tokens**: a reasoning model
writes long replies. Every chunk is embedded **twice**.

### Projected to one 30M-word tenant (one-time; repeated only for changed documents)

| | Tokens |
|---|---|
| AI input | ~168M (about half cached) |
| AI output | ~49M |
| Jina embeddings | ~168M |

Cost = these tokens × the chosen model's and Jina's price per million tokens.
Prices are not filled in here.

## 5. Time, CPU and memory to ingest

- **10–17 minutes per tenant** (median about 13.5), almost all of it waiting on
  the AI and Jina.
- **Our own work: about 5 CPU-seconds and about 100 MB of memory per tenant.**
  Ingest is limited by the outside services, not by our server.

## 6. Questions

100 real questions (90 normal, 10 Deep), one per document, all answered with
citations.

| Per question | AI tokens (in / out) | Jina tokens | Vector searches |
|---|---|---|---|
| Normal | 3,300 / 710 | 5,800 | 1 |
| Deep | 8,600 / 610 | 4,700 | 1 |

- **Vector search: 33 ms average, 57 ms max**, in line with the doc's 15–40 ms.
- **One vector search per question**, not the doc's 2–3.
- **An answer is ready in about 4.6 s, of which about 0.1 s is our own work.**
  The rest is waiting on the AI and Jina.

## 7. What changes in the sizing doc

- **Workload:** about 3.5M vectors for 10 × 30M words, not 0.92M; 1024-dim full
vectors.
- **RAM and storage:** about 8.4 KB of index and 7.8 KB of table per vector; the
hot set is about 38 GB.
- **Instance:** the 32 GB server no longer fits as is. Choose the 64 GB server,
or shrink to fit with halfvec or a rebuilt index (to be measured).
- **New section:** the AI and embedding cost of ingesting, which the doc does not
cover.
- **Query latency:** 33 ms per vector search; 1 search per question, not 2–3.
- **Concurrency:** from the concurrent-user load test (next).

## How it was measured

- A local proxy sits between the app and Ollama and Jina. It forwards every
call, records the provider's own token counts, and labels each call with the
phase or question it belongs to.
- It retries hangs, server errors and rate limits. Time lost to retries counts
as provider time.
- CPU and memory are the ingest process's own usage (`getrusage`), sampled per
phase.
- Sizes come from `pg_relation_size` and `pg_column_size`.
