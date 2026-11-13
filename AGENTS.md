# AGENTS.md

**The project rulebook is [CLAUDE.md](./CLAUDE.md). Read it first.**

It holds the constraints: isolation, grounding, citations, and the gotchas
that have to stay rules. What is live is
[docs/handbook/PRODUCT_STATUS.md](docs/handbook/PRODUCT_STATUS.md); a plain walk through each
feature is [docs/handbook/current-features.md](docs/handbook/current-features.md). How a request
moves through the system is [ARCHITECTURE.md](ARCHITECTURE.md). This file is not a second copy.

## Non-negotiables (the full reasoning is in CLAUDE.md)

- **Tenant isolation.** Every tenant-scoped read/write requires an `org_id`,
  filtered in the query itself — never rely on an index. `workspace_id` nests
  *inside* `org_id` and is always paired with it, never used alone.
- **`org_id` enters a request in exactly one place**: `app/api/deps.py`, from
  the signed session cookie. Never from client input.
- **`chunks` is partitioned per organization, with row-level security.** Pass
  the org to `get_connection(org_id=...)` on every `chunks` query and still
  filter on `org_id` (`all_orgs=True` only for cross-tenant background jobs).
- **Document access has one predicate** (`app/security/visibility.py`), in the
  same `WHERE` as `org_id`, on every retrieval leg, chart and graph walk.
- **Embeddings are `halfvec(1024)`.** Bind a query vector as `halfvec`, never
  `::vector`, or the HNSW index is skipped.
- **Don't weaken grounding.** The 0.35 confidence gate and the strict prompt
  are two independent layers; leave both intact.
- **Citations.** Keep a number only when a retrieved document sat at that block.
  The link is the stored source address, never a URL the model wrote. One source
  is a line under the answer; two or more keep a superscript.
- **New capability = new package** (`base.py` + impl + `factory.py`). An
  orchestrator that only composes existing interfaces skips `base.py`.
- **All config** is a `from_env()` dataclass in `app/config/settings.py`.
  Nothing else reads the environment.
- **Every prompt that carries outside text includes the shared
  prompt-injection rules.** Fence the text in `<<<UNTRUSTED_…>>>` markers, put
  `UNTRUSTED_POLICY` before it and `UNTRUSTED_REMINDER` after it
  (`app/security/untrusted.py`). The rules themselves are plain words in
  `app/security/agents.md`, which the app sends to the model. That file, not
  this one, is what the production model reads.
  `tests/test_untrusted_policy.py` fails if a prompt skips them.
- **Identifiers are masked before text leaves** (`app/privacy`). A new
  outbound client (embedder, reranker, guard) calls `privacy.redact`; a
  detector failure blocks the send.
- **Agent mode writes only through an MCP server an admin added**
  (`app/actions/`, `app/mcphost/` for Slack and Google). Ask, Chart, sync and
  the Slack bot import nothing from `app/actions/`.
- **Rate and AI limits are decided in one place each**:
  `app/security/rate_limit.py` (which buckets a request takes) and
  `app/security/ai_spend.py` (tokens a minute per model, dollars a day).
  Redis holds rate limits only; there is no answer cache.
- **Bound every external walk and mark truncation.** A partial result that
  looks complete is the failure that matters.
- **Update `docs/handbook/PRODUCT_STATUS.md` when a feature ships.** Add a line
  to CLAUDE.md only when a new constraint appears.
