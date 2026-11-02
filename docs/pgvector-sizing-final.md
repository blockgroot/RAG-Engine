# pgvector Sizing for 10 Tenants

30 Oct 2026

**Summary:** The projected workload requires approximately **1.1 million vectors**, a
**9 GB** database, **8.5 GB of RAM** and about **2.5 CPU cores** at peak. A vector search takes about
**15 ms** (median) on a full-size test. A single **db.r7g.xlarge** instance (4 vCPU,
32 GB RAM) meets these requirements at **$397 per month** on-demand.

## Inputs

| Parameter | Value |
|---|---|
| Tenants | 10 |
| Documents per tenant | 1,000 (fixed for the first 6 months) |
| Pages per document | 20 |
| Words per page | 512 |
| Embedding model | bge-m3 (1,024 dimensions) |
| Vector storage | `halfvec` (2 bytes per dimension) |
| Chunk size | 256 tokens |
| Chunk overlap | 40 tokens |
| Peak load | 10 reads/s, 1 write/s |

**Note:** `halfvec` is enabled on the `feat/deployment-setup` branch (2 Nov 2026).
Until that branch is deployed, staging and production store full-precision
`vector(1024)` (4 bytes per dimension). All figures in this document assume
`halfvec(1024)`.

## Results

| Metric | Value |
|---|---|
| New tokens per chunk | **184** (≈ 93 words) |
| Vectors per document | **110** |
| Vectors per tenant | **110,000** |
| Total vectors (10 tenants) | **1.1 million** |
| Database size | **9 GB** |
| Hot data in RAM | **4.5 GB** |
| Total RAM required | **8.5 GB** |
| Vector search latency (median / p95) | **15 ms / 45 ms** |
| Search capacity (4 cores) | **≈ 140 queries/s** (10 required) |
| CPU at peak load | **≈ 2.5 cores** |
| HNSW index build (1.1M vectors) | **≈ 8 minutes** |
| Provisioned storage | **40 GB** |
| Recommended instance | **db.r7g.xlarge (4 vCPU, 32 GB), $397/month** |

## Findings

### 1. Vectors per document

The application's chunker (`chunk_text`) was run on 1,474 real documents, each
normalised to 20 pages of 512 words. Each chunk produces one vector.

| Document type | Documents tested | Vectors per document |
|---|---|---|
| Google Drive documents | 78 | 110 |
| Linear issues | 73 | 104 |
| Slack threads | 603 | 108 |
| Wiki and ticket content | 620 | 109 |
| Technical documents | 100 | 118 |
| **All** | **1,474** | **110** |

Results were consistent across document types (104–118 vectors per document), so
110 is used as the planning figure. A 20-page document contains approximately
20,190 tokens, giving an average of 184 new tokens per chunk.

### 2. Storage per vector

Measured on 8,682 production-format chunks stored as `halfvec`, with the
application's indexes in place.

| Component | Bytes per vector |
|---|---|
| Table row (chunk text, vector, full-text search column) | 4,576 |
| HNSW vector index | 2,725 |
| Full-text and tenant indexes | 561 |
| Supporting tables (documents, graph, charts) | ≈ 500 |
| **Total on disk** | **≈ 8,400** |
| **Hot set held in RAM** (HNSW index + 30% of table rows) | **≈ 4,100** |

At 1.1 million vectors this gives a database size of about 9 GB and a hot set of
about 4.5 GB. The full-size test confirmed this: at 1.1 million vectors the table
measured 5.1 GB and the HNSW index 3.0 GB. Allowing 4 GB for PostgreSQL and the operating system brings the
total RAM requirement to 8.5 GB.

### 3. Query latency at full size

A full-size test database was built with 1.1 million `halfvec` vectors across
10 tenants, generated from the 8,682 real vectors and chunk texts, with the
application's indexes. Searches were filtered to one tenant, with
`hnsw.ef_search = 40` and `hnsw.iterative_scan` enabled.

| Scenario | Median | p95 |
|---|---|---|
| New question, index in memory (normal operation) | 15 ms | 45 ms |
| First questions after a restart (cold cache) | 35 ms | 114 ms |
| Repeated question (cached) | 1.4 ms | 3.5 ms |

- **Capacity:** about 140 new queries per second on 4 cores, against the 10 per
  second required.
- **Tenant filter:** with `hnsw.iterative_scan` off, a tenant's search returned only
  3.9 of the 10 requested results. With it on, all 10 were returned, at a cost of
  about 15 ms.

Measured on a laptop (Apple M2, 8 cores, 16 GB RAM), PostgreSQL 17, pgvector 0.8.6.

### 4. CPU at peak load

| Workload | CPU time per operation | Cores |
|---|---|---|
| Reads: 10 queries/s, each one vector search and one full-text search | ≈ 50 ms | 0.5 |
| Writes: 1 document/s, 220 index inserts (110 vectors, each indexed twice during ingestion) | ≈ 7 ms per insert (measured at 1.1M vectors) | 1.5 |
| Row writes and write-ahead logging | – | 0.2 |
| Allowance for a slower server core than the M2 | – | 0.3 |
| **Total** | | **≈ 2.5** |

Writes dominate. The peak lasts only during bulk ingestion: at 1 document per
second, all 10,000 documents load in about 3 hours. At about 2.5 of 4 vCPUs (≈ 60%)
during that window, and well under 50% otherwise, a 4 vCPU instance is sufficient.

## Impact of halfvec

`halfvec` reduces the database from 19 GB to 9 GB and the RAM requirement from
15.8 GB to 8.5 GB. It does not change the current $397 instance, but it raises the
instance's capacity from about 2 million to about 5.4 million vectors.

## Instance and Cost

Amazon RDS for PostgreSQL, Asia Pacific (Mumbai), single-AZ:

| Instance | vCPU / RAM | On-demand (monthly) | 1-year reserved (monthly) |
|---|---|---|---|
| db.r7g.xlarge | 4 / 32 GB | $397 | $290 |

At peak the instance uses about 2.5 of its 4 vCPUs and 8.5 GB of its 32 GB of RAM.
If CPU must stay below 50% even during bulk ingestion, the next size,
db.r7g.2xlarge (8 vCPU, 64 GB), costs $794 per month.
Storage and backup costs are minimal at this volume.

## Prerequisites

Both are implemented on `feat/deployment-setup` (2 Nov 2026):

- **`halfvec` storage.** `chunks.embedding` and `gap_topics.embedding` are
  `halfvec(1024)`. `apply_schema()` converts existing rows in place on the next
  deploy (no re-embedding) and rebuilds the HNSW index. The conversion locks
  `chunks` for seconds at today's size.
- **`hnsw.iterative_scan = strict_order`** on every pooled connection. Without it, a
  tenant's search returned only 3.9 of 10 requested results in the full-size test.
- **Requires pgvector 0.8 or later** on staging and production for the iterative
  scan. Check with `SELECT extversion FROM pg_extension WHERE extname = 'vector'`.
