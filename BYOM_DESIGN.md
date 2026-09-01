# Bring Your Own Model — design under review

Status: **Phase 1 design, pre-review.** Branch `feat/bring-your-own-model`.

## 1. Understanding Lock (confirmed)

An org admin opens a **"Your model"** tab in the Company section of the left
rail (between Sources and People), enters an LLM provider, model id, base URL
and API key. Once saved *and verified*, that model appears as one extra entry
in the model dropdown for **every member of that org**.

Two jobs: remove the shared free-tier quota as a ceiling, and answer the
enterprise objection *"our internal documents cannot go through a vendor's
shared LLM account"*.

**Confirmed decisions**
- Exactly ONE custom model per org.
- Probe policy: block on `grounds`/`refuses` failure; warn-and-allow on `tools`.
- Presets: OpenAI, Anthropic, OpenRouter, Groq (+ Custom).

**Non-goals (v1):** per-member keys; per-workspace override; replacing the
built-in catalog; schedulers; localhost endpoints.

## 2. Storage — zero schema change

One `oauth_connections` row:

| column | value |
|---|---|
| `provider` | `'llm'` (TEXT, no CHECK constraint) |
| `org_id` | the org |
| `workspace_id` | `NULL` — org-wide |
| `external_workspace_id` | the model id, e.g. `gpt-5` (column is NOT NULL) |
| `external_workspace_name` | display label, e.g. `GPT-5 (company)` |
| `access_token_encrypted` | `crypto.encrypt(api_key)` |
| `source_config` | `{base_url, preset, key_tail, verified_at, checks:{…}}` |

`idx_oauth_connections_org_provider_orgwide` is already
`UNIQUE (org_id, provider) WHERE workspace_id IS NULL`, so "exactly one per
org" is enforced by an existing index. `crypto.encrypt` (MultiFernet, rotatable)
is already the credential-at-rest path for every OAuth token.

## 3. API — all `require_admin`

- `GET /admin/llm-model` → masked config + probe results. **Never the key.**
- `PUT /admin/llm-model` → validate → SSRF-check → probe → encrypt → upsert.
- `DELETE /admin/llm-model` → remove the row.
- `POST /admin/llm-model/test` → probe without saving.

`GET /chat/models` (member-level) appends the org's entry with
`backend: "custom"`, if one exists and verified.

## 4. Resolution path

`app/llm/org_model.py` — `get_org_model(org_id) -> OrgModel | None`, reading and
decrypting the row.

`RoutedLLMProvider` changes:
- the ContextVar carries `(org_id, model_id)`, not `model_id`
- `self._clients` is keyed by `(org_id, model_id)`
- `_client_for()`: catalog hit → today's path unchanged; catalog miss → load the
  org's row, decrypt, build an `OpenAICompatProvider`

Nothing else in the answer path changes. `RagPipeline`, every agent and
`schedulers/runner` stay untouched, exactly as they never learned that model
selection exists.

### The isolation bug this fixes

`self._clients` lives on `RoutedLLMProvider`, held by `lru_cache(maxsize=1)`
agent singletons — **one dict shared by every tenant**, keyed by model id alone.

Today that is sound because every model id is catalogued and its credentials come
from process env, so the id *fully determines* the credentials. BYO breaks that
invariant: two orgs can put different credentials behind the string `gpt-5`.

1. Org A asks → miss → reads org A's row → builds client → `_clients["gpt-5"]`
2. Org B asks → **hit** → gets org A's client
3. Org B's retrieved chunks go to org A's endpoint on org A's key

Org B's correctly-isolated row is never read. Nothing looks wrong.

`query_answer_cache` is already `WHERE org_id = %s` and needs no change.

## 5. Security constraints

**SSRF.** A user-supplied `base_url` makes the server fetch an arbitrary
address: `169.254.169.254` (cloud metadata credentials), `localhost:5432`
(Postgres), any RFC1918 host inside Render's network. Require `https://`,
resolve the hostname, reject loopback/private/link-local/CGNAT/`::1`/IPv4-mapped
v6, and **re-check after DNS resolution**. Disable redirects.

**Admission probe.** A model id says nothing about the three behaviours grounding
depends on: the `MODE: A|B|C` tag, the fallback string compared by equality, and
tool calling for `GitHubAgent`. Reuse `scripts/verify_models.py`'s four probes
against the real grounded prompt with production's `RAG_MAX_ANSWER_TOKENS`.
Store the verdict.

**Key handling.** Encrypted at rest; never returned by any GET; masked tail only;
Replace and Delete, never a populated field.

**Blast radius.** Chat only — `build_aux_llm_provider` stays unwrapped, so
ingestion contextualization, query rewriting and the groundedness audit remain
structurally unroutable.

## 6. Decision log

| # | Decision | Alternatives | Why |
|---|---|---|---|
| D1 | `oauth_connections` row, `provider='llm'` | new `org_llm_models` table | Zero migration; encryption, org cascade and the uniqueness index already exist |
| D2 | Exactly one per org | a curated list | Existing unique index enforces it; list UI is materially more code for no confirmed need |
| D3 | Additive to the catalog | replace built-ins | Their key will break (quota, rotation); leaving your models selectable is the fallback, and is less code |
| D4 | Admin-only, own rail tab | inline in the chat composer | Matches where org credentials already live; keeps the dropdown uncluttered; probe can take its time off the hot path |
| D5 | Block on grounds/refuses, warn on tools | block on any | Tools only degrades `GitHubAgent` to its fixed fallback; grounds/refuses break RAG correctness |
| D6 | Cache key `(org_id, model_id)` | keep `model_id` | Per-org credentials break the invariant that made the old key sound |
| D7 | Org-wide only (`workspace_id NULL`) | per-workspace | YAGNI; the partial index supports adding it later without a migration |

---

# Phase 2 — Review log

Reviewers are hard-scoped: they state defects, they do not redesign.
Status: Skeptic ✅ · User Advocate ✅ · Constraint Guardian ⏳

## Objections raised

### O1 — CRITICAL. The stated purpose is false as scoped.
*(Skeptic; independently H1 from User Advocate; verified by the designer.)*

§1 sells this as answering *"our internal documents cannot go through a vendor's
shared LLM account."* Only the chat generation call routes to the org's key:

- `build_aux_llm_provider` is unwrapped (`app/llm/factory.py:38`), and ingest
  contextualization runs on it (`app/ingestion/pipeline.py:295,473`) — so **every
  chunk of the org's corpus** goes to the deployment's shared key at ingest.
- The audit sends `build_audit_prompt(question, contexts, answer)`
  (`app/rag/pipeline.py:983`) under `default_model_only()` — **retrieved chunks
  and the generated answer**, on the shared key, every Mode A/B request.

§5 files this under "Blast radius" as a *safety* property. It is the same fact
the admin is buying against, addressed to only one of its two audiences.

**Status: BLOCKS the design's framing, not its mechanism. Awaiting the product
decision (drop the privacy claim vs. route aux+audit per-org).**

### O2 — CRITICAL. The `(org_id, model_id)` ContextVar breaks two string consumers.
*(Skeptic.)* `selected_model()` is read outside `routed.py` at
`app/api/chat.py:380` (feeds the `done` SSE `model` field → **would ship the
org_id to the browser**) and `app/rag/query_cache.py:62` (silently reshapes every
cache key, catalogued models included). **Accepted — two separate ContextVars.**

### O3 — HIGH. The designer's "one shared dict" model was wrong.
*(Skeptic.)* Seven `RoutedLLMProvider` instances exist, one per `lru_cache`
agent singleton (`app/api/deps.py:48-128` → `app/agent/factory.py:207`), plus a
throwaway per `/chat/models` (`app/api/chat.py:94`). The isolation bug is real;
the claim that the fix is contained is not. No invalidation on rotate/delete is
specified, though `credentials.py:499` already solves this for GitHub tokens.
**Accepted.**

### O4 — HIGH. `provider='llm'` is not inert. "Zero schema change" has a cost.
*(Skeptic; designer verified.)*
- `app/api/setup_status.py:14` `has_connection` has **no provider filter** →
  saving a model marks onboarding's "Connect" step done with zero sources.
- `app/api/schedulers.py:183` lists providers unfiltered → "llm" renders as a
  connected source.
- `app/api/admin.py:511` `trigger_ingest` rejects only `github` → an API caller
  can enqueue an unrunnable job.
- `app/api/admin.py:193` returns `source_config` wholesale on a second endpoint.
**Accepted.**

### O5 — HIGH. A one-shot probe admits exactly what the catalog rejects.
*(Skeptic.)* `app/llm/catalog.py:76-81` rejects minimax for INTERMITTENT tool
calls — *"'Usually supports tools' is not support."* A single pass admits that
class. `verified_at` records when a coin came up heads. **Accepted.**

### O6 — HIGH. Four sequential probe calls inside a synchronous PUT.
*(Skeptic.)* ~2.3k-token prompt × 4, each bounded only by a 60s timeout
(`openai_provider.py:38`) ≈ 240s worst case. Browsers and proxies drop first.
`app/jobs/` already exists for this. D4's "off the hot path" is not true as
written. **Accepted.**

### O7 — HIGH. `verified` will read as live status; it is a dated snapshot.
*(User Advocate H2.)* Must render past-tense and dated, never a green
"Connected" badge — that badge means *live* everywhere else in this product.
**Accepted.**

### O8 — HIGH. Failure blames the product and strands the member.
*(User Advocate H3.)* `_user_facing_llm_error` (`app/api/chat.py:160`) is written
for the deployment's own quota: *"try again shortly"* is advice that fails
forever on a revoked key. The member is not told it is the company's model, that
another model would answer now, or that only an admin can fix it. The admin is
not told either — `needs_reauth`/`reauth_reason` (`credentials.py:376`) exists
and is unused here. **Accepted.**

### O9 — HIGH. A tools-warned model breaks GitHub answers invisibly.
*(User Advocate H4.)* `GitHubAgent` grounds structurally — no tool call returns
the **fixed fallback**, indistinguishable from "nothing found". D5 saves exactly
such models. The member has no path to discovering why. **Accepted — D5 revised:
the limitation must appear in the member-facing dropdown note AND on the Code
tab; the fixed fallback must not be reused for this case.**

### O10 — MEDIUM. SSRF is not enforceable where the design puts it.
*(Skeptic.)* `openai_provider.py:112` passes no `http_client`, so there is no
seam to pin a validated IP or disable redirects; httpx re-resolves at connect
time, every request — validate-then-connect is a DNS-rebinding TOCTOU. §4's
"nothing else changes" and §5's SSRF bullets are mutually exclusive.
**Accepted — awaiting Constraint Guardian for what is genuinely enforceable.**

### O11 — MEDIUM. `extra_body` for a custom endpoint is unspecified, and both
defaults are wrong. *(Skeptic.)* Omit `data_collection: "deny"` on the OpenRouter
preset and tenant content routes to training providers. Omit
`reasoning.exclude` and a `<think>` preamble defeats `_MODE_TAG_RE`, silently
disabling the audit. Sending either to a non-OpenRouter endpoint is also wrong.
**Accepted — per-preset request shape required.**

### O12 — MEDIUM. `catalog.is_selectable` will 400 the org's own model.
*(Skeptic.)* `app/api/chat.py:471` is the only guard on a client-supplied model
string and takes no `org_id`. Catalog-miss behaviour is undefined for: no row,
different id, unverified row. Today's miss path degrades to the default with a
comment defending it; the design replaces that silently. **Accepted.**

### O13 — MEDIUM. Probe verdicts are engineer vocabulary.
*(User Advocate M1, M2.)* `grounds/refuses/resists/tools` must become
consequences. The raw provider status + message must ALSO reach the admin
verbatim — by this project's own history (CLAUDE.md: 5/5 and 2/3 guessed ids
dead) the typical first save fails, and 401 / 404 / unreachable / SSRF-refused /
behaviour-failed / empty-at-token-cap are five different next actions.
**Accepted.**

### O14 — MEDIUM. Naming and disclosure.
*(User Advocate M3–M6.)* "Your model" is possessive-singular beside the plural
collections *Sources* and *People* → **Model**, with a rail hint. The member's
dropdown entry must lead with the company, not trail it in a parenthetical
(`note` is hover-only, unavailable on touch). Admin must be told before pasting:
write-once, what it is used for, and that **every member spends on their bill**.
SSRF refusals must name the rule, not read as a bug. **Accepted.**

### O15 — LOW. *(Skeptic.)* Machinery-stage metering is attributed to the picked
model (`pipeline.py:289` logs outside the `default_model_only()` block);
pre-existing, but BYOM adds a DB read + Fernet decrypt behind those getters.
`_clients` is also uncapped (`routed.py:149`) — one httpx pool per (org, model)
per instance, forever. **Accepted as follow-ups, not blockers.**

---

# Phase 3 — Arbitration

Constraint Guardian returned **2 BLOCKERS** (F1 redirects, F2 DNS rebinding) and
9 constraints. Designer independently verified F1, F2's premise, F15, O1 and O4.

## The ruling that removes both blockers

**Free-text `base_url` is cut from v1.** The four confirmed presets — OpenAI,
Anthropic, OpenRouter, Groq — have base URLs that are **constants in our code**.
The admin supplies provider + model id + API key only.

Both blockers are consequences of a capability that was never actually chosen:
the "+ Custom" entry was the designer's own addition beyond the confirmed preset
list. With no user-supplied URL there is no SSRF sink, so F1, F2, F3 and F4's
scanning oracle all become non-applicable rather than mitigated.

This is the honest trade, stated plainly: **v1 is "bring your own key", not
"bring your own endpoint."** A self-hosted/internal endpoint is a v2 item that
must carry the socket-level connect check F2 identifies — a validating
`httpcore` network backend — because nothing above that layer can close the
rebinding window. It does not get bolted on later without that work.

## Objection dispositions

| # | Objection | Ruling |
|---|---|---|
| O1 | Purpose false as scoped | **ACCEPTED — blocks framing.** Open product decision; §1 rewritten either way |
| O2 | Tuple ContextVar breaks consumers | ACCEPTED — two separate ContextVars, `selected_model()` keeps returning a string |
| O3 | Seven instances, no invalidation | ACCEPTED — see F12 |
| O4 | `provider='llm'` not inert | ACCEPTED — see F15 |
| O5 | One-shot probe admits intermittents | **PARTIALLY ACCEPTED.** 3 tool-call attempts, all must pass; verdict is dated, re-probe on demand. Full periodic re-probe deferred — cost is real and the failure is visible |
| O6 | Probe blocks a PUT | ACCEPTED — see F9 |
| O7 | `verified` reads as live | ACCEPTED — dated past-tense only, never a "Connected" badge |
| O8 | Failure blames the product | ACCEPTED — distinct member copy + `needs_reauth` stamped on the row |
| O9 | Tools-warned breaks GitHub invisibly | ACCEPTED — D5 amended |
| O10 | SSRF unenforceable | **MOOT** under the ruling above |
| O11 | `extra_body` unspecified | ACCEPTED — per-preset shape; OpenRouter preset keeps `_ROUTING_PREFS` |
| O12 | `is_selectable` 400s the org's model | ACCEPTED — see F13 |
| O13 | Engineer vocabulary | ACCEPTED — consequence language + raw provider error verbatim |
| O14 | Naming/disclosure | ACCEPTED — tab is **Model**; spend consent at save |
| O15 | Metering attribution, uncapped dict | Metering DEFERRED (pre-existing); cap ACCEPTED (F12) |
| F1–F4 | SSRF family | **MOOT** — no user-supplied URL in v1 |
| F5 | MultiFernet correct | No action |
| F6 | `base_url` in logs | ACCEPTED — moot for userinfo; still pin the `openai` logger at INFO |
| F7 | `response.model` echoed | ACCEPTED — truncate at the boundary |
| F8 | `data_collection: deny` lapses | ACCEPTED — OpenRouter preset keeps it; others documented as the org's own contract. **CLAUDE.md §3 must be amended** |
| F9 | 720s probe | ACCEPTED — 10s timeout, `max_retries=0`, hard wall-clock budget, out-of-request |
| F10 | Threadpool DoS | ACCEPTED — `LLM_CUSTOM_TIMEOUT=25`, `max_retries=0` |
| F11 | `_clients` global | Design confirmed correct |
| F12 | No invalidation/bound | ACCEPTED — key includes a config version; LRU bound with `close()`; TTL, since eviction cannot reach other processes |
| F13 | Gate must fail closed | ACCEPTED — a catalog miss with no matching verified org row is a 400, never a fallthrough to the deployment's key |
| F14 | Cache-key stringification | ACCEPTED — moot once O2 keeps the string |
| F15 | Breaks `list_connections` | ACCEPTED — `provider != 'llm'` filter at the store, not per-caller |

## Rejected

Nothing was rejected outright. O5 and O15 are partially deferred with rationale
above; both are visible failures, not silent ones.

## Disposition

**REVISE** — the mechanism (§4 multi-tenancy) is sound and confirmed by two
reviewers. The design proceeds to v2 with the ruling above. One item remains
genuinely open and is the product owner's: whether §1 claims data isolation
(requires routing aux + audit per-org, and a forced re-ingest on model change)
or claims capacity + model choice only.
