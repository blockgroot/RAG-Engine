# Visual Representation (Insights) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A `/visualizations` section where a member sees curated charts per
connected source (GitHub, Linear, Notion, Drive, Slack, Forms), scoped to the
company or one space, filtered by week/month/quarter — plus an ask box that
turns "create a visual representation of task completion in team aggregated by
team" into one of those charts.

**Architecture:** Charts are computed by SQL over a new narrow `activity_facts`
table written by the existing ingest worker, never by the LLM. A hardcoded
**metric registry** (`app/insights/registry.py`) is the semantic layer: ~20
named metrics, each a whitelisted SQL fragment plus allowed dimensions. The
LLM's only job in this feature is to **select** a registry entry and write a
caption. GitHub keeps its structural exception — it embeds nothing, so it reads
live at request time into the *same* row shape, storing no facts.

**Tech Stack:** Postgres (no new extension), FastAPI, psycopg pool,
Next.js 15, hand-rolled SVG charts (no chart library — see D4).

---

## The one invariant

> **Numbers come from SQL over rows we actually stored. The LLM never produces
> a number, a total, an axis or a date.**

A prose answer that is wrong hedges and cites. A bar chart that is wrong reads
as a measurement. Neither the confidence gate (`RAG_SIMILARITY_THRESHOLD`) nor
the strict prompt can check arithmetic, so a chart derived from retrieved chunk
text is unfalsifiable fabrication. Every task below preserves this: if a metric
cannot be computed by counting rows, it is not shipped as a chart.

The single exception is **sentiment** (Phase 5), where the LLM classifies each
individual response once, in the background, and the *classification* is then
stored as a fact and counted like any other. The chart is still a `GROUP BY`.

---

## Why a section, not a chatbot

A second chat box promises open-ended computation over prose — the exact thing
the invariant forbids — and splits the member's history across two boxes they
have to guess between. So: **one section, with a constrained ask box inside
it.** The ask box is a question-to-spec resolver, not a conversation:

```
"visual representation of task completion in team aggregated by team"
   -> one constrained LLM call, structured output
   -> {metric: "issues_completed", group_by: "assignee", period: "month",
       provider: "linear"}
   -> validated against the registry; unknown metric/dimension = refuse
   -> SQL over activity_facts
   -> chart + caption
```

Off-registry asks refuse and list what *is* available, mirroring
`app/api/schedulers.py::_classify_provider`'s "Could not tell which app this
report is about". A refusal, not a wrong chart.

---

## Blocking decisions (answer before Phase 4 and Phase 5)

| # | Decision | Blocks | Default if unanswered |
|---|---|---|---|
| D1 | ~~Form or Sheet?~~ **RESOLVED: Google Forms.** Responses come from the **Forms API** (`forms.googleapis.com/v1/forms/{id}/responses`), which the Drive adapter cannot reach — it needs its own OAuth scope (`forms.responses.readonly`, plus `drive.metadata.readonly` to *find* the forms by `mimeType=application/vnd.google-apps.form`). A new scope means **every tenant re-consents**, so this is a connector change, not a chart change. | Phase 5 | — |
| D2 | ~~Sentiment gating?~~ **RESOLVED: owners-only, minimum 5 responses per bucket.** Rationale is not policy preference, it is arithmetic: on a 6-person team, "3 of 4 responses in Engineering are negative" identifies people. A suppression floor is the only thing that makes an anonymous survey actually anonymous, and it must be a test rather than a convention. | Phase 5 | — |
| D3 | Slack volume source: live `conversations.history` (accurate, slow, rate-limited) or the index (fast, but `SLACK_MIN_THREAD_CHARS=15` drops short threads and ingest stores **threads not messages**, so counts undercount and carry no author)? | Phase 4 | Index + a visible coverage note |
| D4 | Chart rendering: hand-rolled SVG or a library? | Phase 0 Task 8 | **Hand-rolled SVG.** `frontend/` has no Tailwind and no UI kit (plain CSS vars + global classes); bar/line/stacked-bar is ~60 lines each. Revisit only if Phase 3 lets the LLM choose chart *shapes* freely, at which point Vega-Lite becomes the right answer. |

### D5: index the author at sync time, never at view time — **RESOLVED: yes**

Every "by whom" chart in this plan reads a name out of `activity_facts`, which
means the name is fetched **once, during a sync we already run**, and never
during a page load. Audited per adapter — the cost is close to zero because
every source already hands us the author in a request we already make:

| Connector | Where the name comes from | Extra API calls |
|---|---|---|
| Slack | already fetched and cached — `_display_name` (`app/sources/slack.py:183`), used at line 319 | **none.** We fetch it today and discard it |
| Linear | `assignee { name }` already selected (`app/sources/linear.py:78`); add `creator { name }` for who filed it | **none** |
| Drive | add `lastModifyingUser(displayName)` to `_LIST_FIELDS` (`app/sources/google_drive.py:23`) | **none** — same request, more fields |
| Notion | `search` already returns `last_edited_by`, but only the **id** | one `GET /users/{id}` per *distinct* person, cached exactly as Slack caches |
| GitHub | `user.login`, `merged_by.login`, review authors | see D6 |

**Two consequences that must be visible in the UI, not just true in the code:**

1. **No history before the change.** Facts exist from the first sync after
   deploy. A "top editors" bar chart that silently begins on re-ingest day
   looks like the team started working that week. Every chart therefore
   reports `first_fact_at` for its scope, and the frontend renders
   "Measured since 12 Sep" rather than an axis starting from nowhere. A
   backfill is possible for `docs_changed` (from `source_last_modified`, which
   we already store) but **not** for authors — that data was never captured
   and cannot be invented.
2. **This stores per-person activity.** A leaderboard of named colleagues is
   exactly what was asked for, and it is also a different kind of data from a
   page count. It stays inside the existing scope rules (`org_id` +
   `workspace_id` + `assert_member`, so a space's charts never name people
   from outside it), and sentiment keeps the stricter D2 floor on top.

### D6: GitHub writes facts too — **RESOLVED: yes, facts-only sync**

This follows directly from D5 and it *replaces* the live-read design this plan
originally carried for GitHub. Reading GitHub at view time meant paying its
rate limit on every page load, a cold-start-plus-N-API-calls latency, and no
history at all beyond what one cheap call returns.

**It does not break "GitHub embeds nothing."** That guarantee is about vectors:
no `documents` rows, no `chunks`, no embeddings, no `SourceAdapter`. An
`activity_facts` row is a counter, not a chunk — and Phase 1 keeps the
assertion that `chunks` and `documents` stay empty for GitHub, which is where
the guarantee actually lives.

What it changes concretely: GitHub is currently excluded from the sync tick by
`UNSYNCABLE_PROVIDERS = ("github",)` in `app/jobs/autosync.py`, added because a
GitHub ingest job can only ever fail with "Unknown source type". It gets
re-admitted on a **separate facts-only path** — never the ingestion queue —
so that failure mode stays impossible.

**The cost, stated plainly:** GitHub charts become as fresh as the last sync
(≤6h) instead of live. For "PRs merged per week" that is invisible; the
freshness panel discloses it regardless.

---

## What we present, per connector

Every metric below is one registry entry. `dims` = the dimensions a member (or
the ask box) may group by. Every chart accepts the global period filter
(`week` | `month` | `quarter`).

### Notion & Drive — Phase 0 (data already in the DB)

| Metric key | Chart | Dims | Source of truth |
|---|---|---|---|
| `docs_changed` | line, over time | `provider`, `space` | `documents.source_last_modified` |
| `doc_staleness` | histogram (buckets: <7d, 7–30d, 30–90d, >90d) | `provider` | `now() - source_last_modified` |
| `docs_by_space` | bar | `space` | `documents.workspace_id` |
| `corpus_size` | single stat + sparkline | `provider` | `count(documents)` |
| `top_editors` | bar | `actor` | `lastModifyingUser(displayName)` added to Drive's existing `files.list`; Notion's cached `GET /users/{id}` (D5) |

**Why this first:** it needs no new API *call* — only extra fields on requests
we already make (D5) — so it proves the table, the registry, the endpoint, the
scoping and the chart component in the smallest possible diff.

`docs_changed`, `doc_staleness`, `docs_by_space` and `corpus_size` can be
**backfilled** from `documents.source_last_modified`, which is already stored.
`top_editors` cannot: no author was ever captured, so it starts at the first
sync after deploy and says so on the chart.

### GitHub — Phase 1 (facts-only sync; still no vectors, D6)

| Metric key | Chart | Dims | Needs |
|---|---|---|---|
| `prs_opened` | line | `repo`, `actor` | **new** `GET /repos/{r}/pulls?state=all` |
| `prs_merged` | line | `repo`, `actor` | same call, `merged_at` non-null |
| `pr_mergers` | bar | `actor` | **`merged_by.login`** — who merged is a different person from who authored, and gatekeeping concentration is the thing worth seeing |
| `pr_authors` | bar (leaderboard) | `actor` | same call |
| `pr_reviewers` | bar — **bus-factor read** | `actor` | **new** `GET /repos/{r}/pulls/{n}/reviews` |
| `pr_lead_time` | box/percentile bars (p50/p75/p90) | `repo` | `merged_at - created_at` |
| `open_pr_age` | histogram | `repo` | open PRs, `now() - created_at` |
| `commits_by_author` | bar | `repo`, `actor` | already exists — `app/githublive/rest.py:158 list_commits` |

`app/githublive/base.py` currently declares only `list_repos`, `get_readme`,
`get_commit`, `list_commits`. PRs and reviews are genuinely new surface.

The `PullRequest` dataclass must carry **three distinct people** —
`user.login` (raised it), `merged_by.login` (merged it) and the reviewers from
the reviews call. Collapsing any two of them loses the reading that matters: on
most teams one person merges most PRs, and that is invisible if `actor` means
"author" everywhere.

**PR cycle time broken into stages** (coding -> waiting for review -> in review
-> waiting to merge) is the highest-value engineering chart per the DORA
literature, but it needs review *timestamps* per PR — one extra call per PR.
Ship `pr_lead_time` (one number, one call) in Phase 1; stage breakdown is
Phase 1b, bounded to the 20 most recent merged PRs.

### Linear — Phase 2

| Metric key | Chart | Dims | Needs |
|---|---|---|---|
| `issues_completed` | line (throughput) | `assignee`, `team`, `label` | adapter already fetches `state`, `state_type`, `assignee` (`app/sources/linear.py:73-78`) |
| `issue_states` | funnel / stacked bar | `team` | same |
| `assignee_load` | bar | `assignee` | same |
| `issue_aging` | histogram of open issues | `team` | same |
| `issue_cycle_time` | percentile bars | `team` | **needs `createdAt` + `completedAt` added to the GraphQL selection** |

`issues_completed` grouped by `team` is the user's literal example request and
is the acceptance criterion for Phase 2.

### Slack — Phase 4 (gated on D3)

| Metric key | Chart | Dims |
|---|---|---|
| `messages_volume` | line | `channel` |
| `active_hours` | heatmap (day x hour) | `channel` |
| `channel_share` | bar | `channel` |
| `thread_response_time` | percentile bars | `channel` |

Every Slack chart carries a mandatory coverage note, the same discipline as
`app/schedulers/activity.py`'s notes: an undercount that looks complete is the
failure that matters.

### Forms — Phase 5 (gated on D1, D2)

| Metric key | Chart | Dims |
|---|---|---|
| `sentiment_by_theme` | **diverging stacked bar**, neutral centred, sorted by favourable | `theme` |
| `sentiment_over_time` | line, net favourable | `theme` |
| `theme_volume` | ranked bar | `theme` |

Diverging stacked bar is the standard for Likert/agree-disagree data — it puts
every item on a shared neutral baseline so the lean is readable at a glance.
**Never** a single company happiness score, **never** a per-respondent row, and
any bucket under 5 responses is suppressed (D2).

### Cross-provider — Phase 0 Task 7

| Metric key | Chart | Why |
|---|---|---|
| `connector_freshness` | one row per connector: last synced, status | Highest-trust chart in the product. `oauth_connections.last_sync_at` and `needs_reauth` already exist. Answers "is this dashboard even current?" before anyone reads a number off it. |

---

## Phases

| Phase | Delivers | Blocked by |
|---|---|---|
| **0** | `activity_facts`, registry, `/insights` API, `/visualizations` page, chart component, Notion/Drive + freshness | nothing |
| **1** | GitHub PR/review live metrics | Phase 0 |
| **2** | Linear facts + `issues_completed` by team | Phase 0 |
| **3** | The ask box (question -> spec), pinning, charts inline in Ask | Phase 0 |
| **4** | Slack | D3 |
| **5** | Forms connector, then sentiment | D1, D2 |

Phases 0–2 are fully stepped below. Phases 3–5 are task-level: their shape
depends on decisions above and on what Phase 0 teaches, and writing speculative
code for them now would be exactly the over-engineering this codebase avoids.
**Expand a phase when you reach it, not before.**

---

# Phase 0 — the spine

## Task 1: The `activity_facts` table

**Files:**
- Modify: `app/db/schema.sql` (append after `scheduler_reports`, near line 600)
- Test: `tests/test_insights_store.py`

One narrow table for every provider. Not one table per provider: the whole
point is that a cross-provider question ("task completion across Linear and
GitHub") is one `GROUP BY` rather than a join nobody wrote.

**Step 1: Write the schema**

Append to `app/db/schema.sql`:

```sql
-- One countable event from a connected source. The ONLY numeric substrate for
-- /visualizations: every chart in the product is a GROUP BY over this table,
-- so no chart can contain a number an LLM invented.
--
-- Deliberately narrow and denormalized. A wide per-provider table (pr_author,
-- issue_assignee, message_channel) makes every cross-provider chart a join
-- somebody has to write; one `actor`/`subject`/`state` triple makes it a
-- WHERE clause. `value` carries the one number a fact may have (a lead time in
-- seconds, a sentiment score) and is NULL for pure count facts.
--
-- Scoped like every other tenant table: org_id always, workspace_id nested
-- inside it (NULL = org-wide).
--
-- GitHub DOES write rows here, and that is not a break with "GitHub embeds
-- nothing": that rule is about vectors -- no documents, no chunks, no
-- embeddings, no SourceAdapter -- and a counter is not a chunk. Facts are
-- written by a facts-only sync path that never touches the ingestion queue,
-- so "Unknown source type: github" stays impossible.
CREATE TABLE IF NOT EXISTS activity_facts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,          -- notion | google | linear | slack | forms
    kind         TEXT NOT NULL,          -- doc_changed | issue_completed | ...
    actor        TEXT,                   -- person, when the source tells us
    subject      TEXT,                   -- repo | channel | team | doc title
    state        TEXT,                   -- issue state, sentiment label
    occurred_at  TIMESTAMPTZ NOT NULL,
    value        NUMERIC,                -- lead time seconds, score; NULL = count-only
    url          TEXT,
    external_id  TEXT,                   -- for idempotent re-ingest
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every read is "this scope, this provider, this window" -- the index mirrors
-- that exactly.
CREATE INDEX IF NOT EXISTS idx_activity_facts_scope
    ON activity_facts (org_id, provider, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_facts_space
    ON activity_facts (org_id, workspace_id, provider, occurred_at DESC);

-- Re-ingesting the same document must not double every count. Partial unique
-- indexes because NULL workspace_id means "org-wide" and Postgres treats
-- NULLs as distinct in a plain UNIQUE (the same trap oauth_connections hit).
CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_facts_org
    ON activity_facts (org_id, provider, kind, external_id)
    WHERE workspace_id IS NULL AND external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_facts_space
    ON activity_facts (org_id, workspace_id, provider, kind, external_id)
    WHERE workspace_id IS NOT NULL AND external_id IS NOT NULL;
```

**Step 2: Verify against a throwaway database**

`ALTER TABLE ... ADD COLUMN` placement bugs are invisible on a migrated DB and
break a fresh one, so this is not optional.

Run:
```bash
createdb handbook_schema_check 2>/dev/null || true
DATABASE_URL=postgresql://localhost/handbook_schema_check python -m app.db.migrate
psql handbook_schema_check -c "\d activity_facts"
```
Expected: the table plus four indexes, no errors.

**Step 3: Commit**

```bash
git add app/db/schema.sql
git commit -m "feat(insights): add activity_facts, the only numeric substrate for charts"
```

---

## Task 2: The metric registry

**Files:**
- Create: `app/insights/__init__.py`
- Create: `app/insights/registry.py`
- Test: `tests/test_insights_registry.py`

Per `CLAUDE.md` §2, `app/insights/` is an **orchestrator** composing existing
interfaces (db pool + the LLM already built), so it gets **no `base.py`** —
there is no second backend to abstract over. Do not add one.

**Step 1: Write the failing test**

`tests/test_insights_registry.py`:

```python
"""The registry IS the semantic layer -- these tests are what stop a chart
containing a number nobody computed.

No DB, no network: the subject is the shape of the definitions themselves.
"""

from __future__ import annotations

import pytest

from app.insights import registry


def test_every_metric_declares_its_provider_and_chart():
    assert registry.METRICS, "an empty registry means the ask box can only refuse"
    for key, metric in registry.METRICS.items():
        assert metric.key == key, f"{key} disagrees with its own key"
        assert metric.provider, f"{key} has no provider"
        assert metric.chart in registry.CHART_TYPES, f"{key} wants an unknown chart"
        assert metric.label, f"{key} has no human label"


def test_no_metric_interpolates_anything_into_its_sql():
    """A registry entry is a fixed fragment. The moment one accepts an f-string
    hole, the semantic layer is a SQL injection surface with extra steps."""
    for key, metric in registry.METRICS.items():
        assert "{" not in metric.select, f"{key}.select has a format hole"
        assert "%s" not in metric.select, f"{key}.select takes a parameter"


def test_dimensions_are_whitelisted_column_names():
    """group_by reaches SQL as an identifier, so it can never come from user
    text -- only from this fixed set."""
    for key, metric in registry.METRICS.items():
        for dim in metric.dims:
            assert dim in registry.DIMENSIONS, f"{key} allows unknown dim {dim!r}"


def test_periods_are_a_closed_set():
    """date_trunc's first argument is an identifier-ish literal; a caller-
    supplied one is an injection. Three values, forever."""
    assert set(registry.PERIODS) == {"week", "month", "quarter"}


def test_a_metric_can_be_looked_up_by_provider():
    notion = registry.for_provider("notion")
    assert notion, "Phase 0 ships Notion metrics"
    assert all(m.provider == "notion" for m in notion)


def test_an_unknown_metric_is_not_silently_invented():
    with pytest.raises(KeyError):
        registry.get("definitely_not_a_metric")
```

**Step 2: Run it to verify it fails**

Run: `pytest tests/test_insights_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.insights'`

**Step 3: Write the implementation**

`app/insights/__init__.py`:

```python
"""Charts over connected-source data.

An orchestrator, not a provider: it composes the db pool and the existing LLM
interface, so per CLAUDE.md there is deliberately no ``base.py`` here.
"""
```

`app/insights/registry.py`:

```python
"""The metric registry -- this codebase's semantic layer.

Why a hardcoded registry instead of letting the model write SQL: pointed at raw
tables, an LLM re-derives the grain, the joins and the metric definition on
every prompt, so the same question returns different numbers. Worse, a wrong
number arrives as a bar chart, which reads as a measurement rather than an
answer -- and neither the confidence gate nor the strict prompt can check
arithmetic. So the model SELECTS from this list and never computes.

Same discipline as ``app/llm/catalog.py``: a small, admitted, hand-kept set.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Chart shapes the frontend can actually draw (see Task 8). A metric may not
#: name one we cannot render -- a registry entry that resolves to a blank panel
#: is worse than one that does not exist.
CHART_TYPES = ("line", "bar", "stacked_bar", "histogram", "stat", "table")

#: The ONLY groupings any metric may accept. ``group_by`` becomes a SQL
#: identifier, so it can never originate in user text -- only here.
DIMENSIONS = {
    "actor": "actor",
    "subject": "subject",
    "state": "state",
    "provider": "provider",
    "space": "workspace_id",
}

#: ``date_trunc``'s unit. A closed set because it reaches SQL as a literal.
PERIODS = ("week", "month", "quarter")


@dataclass(frozen=True)
class Metric:
    """One named, countable thing.

    ``select`` is a fixed aggregate fragment over ``activity_facts`` -- fixed,
    never formatted, because a format hole here is an injection surface. The
    scoping (org, workspace, window) and the grouping are added by
    ``store.run_metric`` from parameters, never by string building.
    """

    key: str
    provider: str
    label: str
    chart: str
    kind: str                       # the activity_facts.kind it counts
    select: str = "count(*)"
    dims: tuple[str, ...] = ()
    unit: str = ""
    #: Shown under the chart. Where a metric cannot see everything (a filter
    #: upstream, a cap), say so here -- a partial chart that looks complete is
    #: the failure that matters.
    caveat: str = ""


METRICS: dict[str, Metric] = {}


def _add(metric: Metric) -> None:
    METRICS[metric.key] = metric


# --- Notion & Drive: countable from data already in `documents` -------------

_add(Metric(
    key="docs_changed",
    provider="notion",
    label="Pages created or edited",
    chart="line",
    kind="doc_changed",
    dims=("space",),
))
_add(Metric(
    key="doc_staleness",
    provider="notion",
    label="How current the pages are",
    chart="histogram",
    kind="doc_changed",
    dims=(),
    caveat="Buckets by when the source last changed, not when we last synced.",
))
_add(Metric(
    key="drive_docs_changed",
    provider="google",
    label="Files created or edited",
    chart="line",
    kind="doc_changed",
    dims=("space",),
))
_add(Metric(
    key="drive_staleness",
    provider="google",
    label="How current the files are",
    chart="histogram",
    kind="doc_changed",
))


def get(key: str) -> Metric:
    """Look one up, raising rather than inventing.

    ``KeyError`` is the point: the ask box turns it into "I cannot chart that,
    here is what I can", which is a refusal instead of a wrong chart.
    """
    return METRICS[key]


def for_provider(provider: str) -> list[Metric]:
    return [m for m in METRICS.values() if m.provider == provider]
```

**Step 4: Run the tests**

Run: `pytest tests/test_insights_registry.py -v`
Expected: PASS (6 passed)

**Step 5: Commit**

```bash
git add app/insights tests/test_insights_registry.py
git commit -m "feat(insights): a hardcoded metric registry, so no chart holds an invented number"
```

---

## Task 3: Running a metric (with isolation proved)

**Files:**
- Create: `app/insights/store.py`
- Test: `tests/test_insights_store.py`

**Step 1: Write the failing test**

`tests/test_insights_store.py`:

```python
"""Charts are a tenant read, so they answer to the same isolation rule as
retrieval: org_id and workspace_id are a WHERE clause on every query.

A missing predicate here LEAKS rather than fails -- the chart still renders,
just with another company's counts in it -- so both directions are pinned.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.insights import store


def _fact(conn, org_id, *, workspace_id=None, provider="notion",
          kind="doc_changed", subject="a page", when=None, external_id=None):
    conn.execute(
        """
        INSERT INTO activity_facts
            (org_id, workspace_id, provider, kind, subject, occurred_at, external_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (org_id, workspace_id, provider, kind, subject,
         when or datetime.now(timezone.utc), external_id),
    )


def test_a_metric_counts_only_the_asking_org(db, org_a, org_b):
    with db() as conn:
        _fact(conn, org_a)
        _fact(conn, org_a)
        _fact(conn, org_b)
        conn.commit()

    rows = store.run_metric("docs_changed", org_id=org_a, workspace_id=None,
                            period="month", days=90)
    assert sum(r.value for r in rows) == 2, "org_b's row must be invisible"


def test_a_space_sees_only_its_own_rows(db, org_a, space_a):
    """Never also org-wide ones -- a meeting-notes space blending in HR policy
    is what makes membership meaningless."""
    with db() as conn:
        _fact(conn, org_a, workspace_id=None)          # org-wide
        _fact(conn, org_a, workspace_id=space_a)       # the space's own
        conn.commit()

    rows = store.run_metric("docs_changed", org_id=org_a,
                            workspace_id=space_a, period="month", days=90)
    assert sum(r.value for r in rows) == 1


def test_the_org_scope_excludes_a_space_s_rows(db, org_a, space_a):
    """The mirror direction. Without it, a predicate that matched everything
    would pass the test above."""
    with db() as conn:
        _fact(conn, org_a, workspace_id=None)
        _fact(conn, org_a, workspace_id=space_a)
        conn.commit()

    rows = store.run_metric("docs_changed", org_id=org_a, workspace_id=None,
                            period="month", days=90)
    assert sum(r.value for r in rows) == 1


def test_rows_outside_the_window_are_excluded(db, org_a):
    with db() as conn:
        _fact(conn, org_a, when=datetime.now(timezone.utc))
        _fact(conn, org_a, when=datetime.now(timezone.utc) - timedelta(days=400))
        conn.commit()

    rows = store.run_metric("docs_changed", org_id=org_a, workspace_id=None,
                            period="month", days=90)
    assert sum(r.value for r in rows) == 1


def test_an_unknown_period_is_refused_not_interpolated(db, org_a):
    """date_trunc's unit reaches SQL as a literal, so a caller-supplied one is
    an injection. It must raise, not sanitize-and-continue."""
    with pytest.raises(ValueError):
        store.run_metric("docs_changed", org_id=org_a, workspace_id=None,
                         period="week'); DROP TABLE activity_facts; --", days=30)


def test_an_unknown_dimension_is_refused(db, org_a):
    with pytest.raises(ValueError):
        store.run_metric("docs_changed", org_id=org_a, workspace_id=None,
                         period="week", days=30, group_by="; DROP TABLE x")


def test_an_empty_result_is_empty_not_a_fabricated_zero_series(db, org_a):
    """A chart with no data must render 'nothing yet', which the frontend can
    only tell apart from real zeroes if we return no rows at all."""
    rows = store.run_metric("docs_changed", org_id=org_a, workspace_id=None,
                            period="month", days=30)
    assert rows == []
```

> **Fixtures:** `db`, `org_a`, `org_b`, `space_a` already exist in
> `tests/conftest.py` (the same ones `tests/test_isolation.py` uses). Read that
> file before writing this test — do **not** invent new fixtures.

**Step 2: Run it to verify it fails**

Run: `pytest tests/test_insights_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'store'`

**Step 3: Write the implementation**

`app/insights/store.py`:

```python
"""Execute a registry metric as one scoped, parameterized aggregate.

The whole query is assembled from three sources and no others: a fixed
fragment from the registry, an identifier looked up in
``registry.DIMENSIONS``, and parameters. Nothing a caller typed ever reaches
the SQL text -- which matters more here than usual, because ``period`` and
``group_by`` are grammatically identifiers, so they cannot be passed as %s.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.exceptions import ProviderError
from ..db.connection import get_connection
from . import registry


@dataclass(frozen=True)
class Point:
    """One bar, one dot. ``bucket`` is the time bucket, ``group`` the series."""

    bucket: str
    group: str | None
    value: float


def run_metric(
    key: str,
    *,
    org_id: str,
    workspace_id: str | None,
    period: str,
    days: int = 90,
    group_by: str | None = None,
) -> list[Point]:
    """Count one metric in one scope over one window.

    ``workspace_id=None`` means org-wide and is NOT "any workspace": a space
    sees only its own rows and the org scope sees only org-wide ones. That is
    the Workspace-within-a-Workspace rule, and it is a WHERE clause here rather
    than a filter afterwards, so isolation never depends on the caller.
    """
    metric = registry.get(key)

    if period not in registry.PERIODS:
        raise ValueError(f"unknown period {period!r}; expected one of {registry.PERIODS}")
    if group_by is not None and group_by not in registry.DIMENSIONS:
        raise ValueError(f"unknown dimension {group_by!r}")

    column = registry.DIMENSIONS[group_by] if group_by else None
    # Both are looked-up constants at this point, never caller text.
    grouped = f", {column}" if column else ""
    selected = f", {column}::text" if column else ", NULL::text"

    scope = (
        "AND workspace_id IS NULL" if workspace_id is None
        else "AND workspace_id = %(workspace_id)s"
    )

    sql = f"""
        SELECT date_trunc('{period}', occurred_at) AS bucket{selected},
               {metric.select} AS value
          FROM activity_facts
         WHERE org_id = %(org_id)s
           AND provider = %(provider)s
           AND kind = %(kind)s
           AND occurred_at >= now() - make_interval(days => %(days)s)
           {scope}
         GROUP BY bucket{grouped}
         ORDER BY bucket
    """

    params = {
        "org_id": org_id,
        "provider": metric.provider,
        "kind": metric.kind,
        "days": days,
        "workspace_id": workspace_id,
    }

    try:
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception as exc:  # noqa: BLE001 - re-raised as our own type
        raise ProviderError(f"insights: metric {key} failed", cause=exc) from exc

    return [
        Point(bucket=r[0].isoformat(), group=r[1], value=float(r[2] or 0))
        for r in rows
    ]
```

**Step 4: Run the tests**

Run: `pytest tests/test_insights_store.py -v`
Expected: PASS (7 passed)

> Per `MEMORY.md`: run **only this file**. The full suite stalls on the remote
> DB and the 15 rpm LLM quota.

**Step 5: Commit**

```bash
git add app/insights/store.py tests/test_insights_store.py
git commit -m "feat(insights): scoped metric execution, isolation pinned both ways"
```

---

## Task 4: Writing facts from documents

**Files:**
- Create: `app/insights/facts.py`
- Test: `tests/test_insights_facts.py`

**Step 1: Write the failing test**

```python
"""Facts are derived from what ingest already stored, so a chart can never
show something the index does not contain."""

from __future__ import annotations

from app.insights import facts


def test_a_document_becomes_one_fact(db, org_a):
    # ... insert a `documents` row with source_provider='notion' and a
    # source_last_modified, then:
    written = facts.record_document_facts(org_a, provider="notion", workspace_id=None)
    assert written == 1


def test_re_ingesting_the_same_document_does_not_double_the_count(db, org_a):
    """The unique index on external_id is the guard; this proves the writer
    actually relies on it (ON CONFLICT) rather than inserting blindly."""
    facts.record_document_facts(org_a, provider="notion", workspace_id=None)
    facts.record_document_facts(org_a, provider="notion", workspace_id=None)
    # count stays 1 -- assert via store.run_metric


def test_a_document_with_no_last_modified_is_skipped_not_dated_now(db, org_a):
    """Stamping now() would invent an edit that never happened, and it would
    pile every undated doc onto today's bar."""
    # insert a document with source_last_modified = NULL
    assert facts.record_document_facts(org_a, provider="notion", workspace_id=None) == 0


def test_github_is_never_written_as_a_fact(db, org_a):
    """GitHub embeds nothing and reads live. A fact row for it would be the
    first crack in that guarantee, so the writer refuses the provider outright."""
    import pytest
    with pytest.raises(ValueError):
        facts.record_document_facts(org_a, provider="github", workspace_id=None)
```

**Step 2–4:** implement `record_document_facts` as one
`INSERT ... SELECT ... FROM documents ... ON CONFLICT DO NOTHING`, refusing
`github` explicitly, skipping `source_last_modified IS NULL`. One statement, no
Python loop — the rows are already in the same database.

**Step 5: Commit**

```bash
git commit -m "feat(insights): derive doc facts from the index, idempotently"
```

---

## Task 5: Hook it into the worker

**Files:**
- Modify: `app/jobs/worker.py:130` (right after `queue.mark_succeeded`)
- Test: `tests/test_insights_facts.py` (add one case)

Facts are written where the answer cache is already cleared — the one place a
successful ingest is known to have happened. Wrap it in its own `try/except`:
a facts-writing failure must cost a chart, never the ingest.

```python
        queue.mark_succeeded(job.id, result.documents_ingested)
        _clear_answer_cache(job.org_id, job.id)
        # Charts are derived from what we just stored. Isolated in its own
        # try/except on purpose: a failure here costs a stale chart, and must
        # never fail an ingest that already succeeded.
        try:
            record_document_facts(job.org_id, provider=job.provider,
                                  workspace_id=job.workspace_id)
        except Exception:  # noqa: BLE001 - breadth is the point
            logger.warning("insights: could not record facts for job %s", job.id,
                           exc_info=True)
```

**Test:** assert a successful `run_once()` leaves facts behind, **and** that a
raising `record_document_facts` still leaves the job `succeeded`.

**Commit:** `feat(insights): record facts on a successful ingest`

---

## Task 6: The API

**Files:**
- Create: `app/api/insights.py`
- Modify: `app/api/main.py:210` (add `include_router`)
- Test: `tests/test_api_insights.py`

Three routes, all member-level:

| Route | Returns |
|---|---|
| `GET /insights/scopes` | the company + every space the member is in, each with its connected providers — reuse `app/api/schedulers.py::_spaces` and `_connected_providers` rather than writing a second copy |
| `GET /insights/dashboard?scope=&period=` | every metric available in that scope, each already computed — one round trip, because a page of 6 charts firing 6 requests on a cold Render instance is a 6× cold start |
| `GET /insights/freshness?scope=` | last sync + `needs_reauth` per connector |

`org_id` comes **only** from `get_session`, never a query parameter. A
`workspace_id` scope goes through `workspaces/store.py::assert_member` before
any query runs — that is the one place membership is validated, and this must
not become a second.

**Tests:** a member of org A asking for org B's scope gets 403/404, not data; a
non-member asking for a space gets refused; an unknown metric key 400s; the
dashboard of a tenant with no connections returns an empty list rather than an
error.

**Commit:** `feat(insights): member-level dashboard API, scoped by session`

---

## Task 7: The page

**Files:**
- Create: `frontend/app/visualizations/page.tsx`
- Modify: `frontend/components/AppShell.tsx:195` (nav entry)
- Modify: `frontend/lib/api.ts` (`getInsightScopes`, `getInsightDashboard`)
- Modify: `frontend/app/globals.css`

```
/visualizations
  scope: [The company v]     period: [Week | Month | Quarter]
  Connector freshness ....... last synced per source
  Notion ....... pages changed | staleness
  Drive ........ files changed | staleness
```

Scope picker is the schedulers one **verbatim**, `<optgroup>` and all — a
second vocabulary for the same distinction is how "org vs personal space" got
confusing the first time. Period is one control governing every chart on the
page.

Empty states must distinguish three different things, because collapsing them
is what makes a dashboard untrustworthy:

- **nothing connected** — "Connect Notion to see this" + a link
- **connected, never synced** — "Waiting for the first sync"
- **synced, no activity in this window** — "No changes this month"

**Commit:** `feat(visualizations): the Insights section, scoped and filtered`

---

## Task 8: The chart component

**Files:**
- Create: `frontend/components/Chart.tsx`
- Test: `frontend/components/Chart.test.tsx` **only if** test infra exists —
  it currently does not (see Risks), so the check is `tsc --noEmit` plus the
  documented manual pass below.

Hand-rolled SVG, three shapes: `line`, `bar`, `stacked_bar` (a histogram is a
bar chart with fixed buckets; a `stat` is a number). No chart library — this
frontend has no UI kit and a 325MB dependency already cost this project a
deploy once.

Must handle: no data, one point (a line through one point is a dot, not a
crash), long labels (truncate + `title`), and horizontal overflow inside its
own `overflow-x: auto` container.

**Manual pass, recorded in the commit:** load `/visualizations` as an org
member and as a space member; confirm the space's numbers differ from the
company's and contain none of its org-wide rows.

**Commit:** `feat(visualizations): SVG line/bar/stacked charts, no new dependency`

### Task 8b (optional, after Task 8): top editors

Add `lastModifyingUser` to `app/sources/google_drive.py:23`'s `_LIST_FIELDS`
and `last_edited_by` to the Notion page fetch, store it as
`activity_facts.actor`, then register `top_editors` with `dims=("actor",)`.
Separate task because it is the only Phase 0 metric needing an adapter change,
and because attributing edits to named people is a change in kind — worth
being deliberate about, not a side effect of a chart.

---

# Phase 1 — GitHub, live

## Task 9: PRs and reviews on the reader

**Files:**
- Modify: `app/githublive/base.py` (add `PullRequest`, `Review` dataclasses +
  two abstract methods)
- Modify: `app/githublive/rest.py` (implement, reusing `_request` at line 207)
- Test: `tests/test_githublive_pulls.py`

Bound **every** walk and **mark truncation** — the existing `list_commits`
(line 158) is the pattern to copy, including its per-page cap and its reason.
Reviews are one call *per PR*, so cap the PR set first (20 most recent) and add
a coverage note when the cap bites, exactly as the scheduler's fetchers do.

404 means "not found or not accessible", never "deleted", and is never retried.

**Tests:** a bounded page count; the truncation marker appears when capped; a
404 raises the not-accessible message; a 403 rate-limit backs off once.

**Commit:** `feat(githublive): bounded pull-request and review reads`

## Task 10: GitHub facts, on a facts-only sync path

**Files:**
- Create: `app/insights/github_facts.py`
- Modify: `app/jobs/autosync.py` (`UNSYNCABLE_PROVIDERS` — see below)
- Modify: `app/insights/registry.py` (8 entries from the catalogue above)
- Test: `tests/test_insights_github.py`

Three distinct `kind` values so the three different people stay separate:
`pr_opened` (actor = `user.login`), `pr_merged` (actor = `merged_by.login`),
`pr_reviewed` (actor = the review author). `subject` = repo. `value` = lead
time in seconds on `pr_merged`.

**The autosync change is the delicate part.** `UNSYNCABLE_PROVIDERS =
("github",)` exists because a GitHub *ingestion* job can only fail with
"Unknown source type" — that stays true and the constant stays. GitHub gets a
**second, separate** branch in the tick that calls `record_github_facts()`
directly and never enqueues an `ingestion_jobs` row.

**Step: write these tests first — they are the guarantee, not a formality**

```python
def test_github_still_never_reaches_the_ingestion_queue(db, org_a):
    """The reason UNSYNCABLE_PROVIDERS exists. A facts path must not quietly
    re-open the door that produced 'Unknown source type: github' in prod."""
    autosync.run_tick()
    jobs = _jobs_for(org_a, provider="github")
    assert jobs == []


def test_github_writes_facts_but_no_documents_and_no_chunks(db, org_a):
    """'GitHub embeds nothing' is about VECTORS. Facts are counters. This test
    is where that distinction is enforced, so nobody has to remember it."""
    record_github_facts(org_a, workspace_id=None)
    assert _count("activity_facts", org_a, provider="github") > 0
    assert _count("documents", org_a, source_provider="github") == 0
    assert _count_chunks(org_a, provider="github") == 0


def test_a_failing_github_read_does_not_fail_the_tick(db, org_a):
    """One dead installation must not stop every other tenant's sync."""
    # patch the reader to raise; assert run_tick() returns normally and the
    # other providers were still queued.
```

**Commit:** `feat(insights): GitHub facts on a facts-only sync path, no vectors`

## Task 11: The GitHub panel

Frontend panel. Every GitHub chart discloses **when it was last synced** and
any cap that bit — the same discipline as the scheduler's coverage notes. The
three people (raised / merged / reviewed) are three separate charts, never one
"activity by person" bar that silently sums them.

**Commit:** `feat(visualizations): GitHub panel, three distinct roles`

---

# Phase 2 — Linear

## Task 12: Add `createdAt` / `completedAt` to the GraphQL selection

`app/sources/linear.py:73-78` already selects `identifier`, `state {name type}`
and `assignee {name}`. Cycle time needs two more scalars. Pass filters as one
`IssueFilter` variable — Linear renamed the inner scalar, and splitting them
is how that bites.

**Commit:** `feat(linear): select createdAt/completedAt for cycle time`

## Task 13: Linear facts

`kind` values: `issue_created`, `issue_completed`, `issue_state_changed`.
`actor` = assignee, `subject` = team, `state` = state name,
`value` = cycle time seconds on completion.

**Commit:** `feat(insights): Linear facts from the existing structured query`

## Task 14: The five Linear metrics

Registry entries per the catalogue. **Acceptance criterion for this phase:**
`issues_completed` grouped by `team` renders — that is the user's literal
request ("task completion in team aggregated by team").

**Commit:** `feat(insights): Linear throughput, states, load, aging, cycle time`

---

# Phase 3 — the ask box

Task-level only; expand when Phase 0 is live and the registry has real shape.

| Task | Files | The hard part |
|---|---|---|
| 15 | `app/insights/resolve.py` | One constrained LLM call, structured output `{metric, group_by, period}`. **Validate against the registry and refuse on a miss** — an off-registry answer is a refusal listing what exists, never a nearest guess. Do not route this through `RagPipeline`: there is no retrieval, no gate, no grounding prompt involved. |
| 16 | `app/api/insights.py` | `POST /insights/ask`. Rate-limited on the trusted forwarded header, like every other LLM route. |
| 17 | `frontend/app/visualizations/page.tsx` | The ask box + `[Pin]`. Follow-ups patch the previous spec (`period`, `group_by`) rather than re-resolving — one turn, no conversation, no drift. |
| 18 | `app/db/schema.sql` | `insight_pins`, scoped `(org_id, user_id)` like `schedulers` — a pin is personal, and nothing here is published to anyone. |
| 19 | `app/agent/routing.py` | Metric intent in Ask -> render the chart inline. Last, and only if the section is already earning its keep. Same component; a misroute costs a normal grounded answer. |

**Model selection note:** resolution is an interactive call, so it goes through
`RoutedLLMProvider` and honours the member's model choice. It must **not** use
`build_aux_llm_provider` — that provider is deliberately unwrapped, and
borrowing it here would make chart resolution silently unroutable.

---

# Phase 4 — Slack (blocked on D3)

Metrics per the catalogue. Whichever source D3 picks, **every Slack chart
carries a coverage note**: from the index, `SLACK_MIN_THREAD_CHARS=15` drops
short threads and ingest stores threads rather than messages, so there is no
author attribution and volume undercounts. An undercount that looks complete is
the failure that matters.

`active_hours` needs a heatmap — the first chart shape Task 8 does not cover.
Ship the other three first and decide then.

---

# Phase 5 — Forms + sentiment (blocked on D1, D2)

Ordered, because each step is worthless without the one before:

1. **A connector.** Google Forms responses live in a Sheet; the Drive adapter
   exports Docs to markdown and never touches Sheets. This is a new
   `SourceAdapter` (or a Sheets branch in the existing one), which is a
   package-shaped change per `CLAUDE.md` §2 — not a visualization task.
2. **Per-response classification.** One LLM call per response, batched, in the
   background on the aux endpoint, with the response text fenced and scrubbed
   through `app/security/untrusted.py` — a survey answer is untrusted input
   like any other. Store label + score as facts. Classify **once**, never per
   page load.
3. **The charts.** Diverging stacked bar for Likert; ranked bars for theme
   volume; net-favourable line over time.
4. **The privacy floor (D2).** Owners-only, suppress any bucket under 5
   responses, never a per-respondent row, never a single company-wide score.
   Write this as a test, not a convention.

---

## Risks and how each is contained

| Risk | Containment |
|---|---|
| **A chart shows another tenant's numbers.** The worst possible bug: it renders normally. | `org_id` + `workspace_id` are a WHERE clause inside `run_metric`, not a filter applied by callers; `tests/test_insights_store.py` pins both directions, so a predicate matching everything cannot pass. |
| **The LLM invents a number.** | It never receives one to manipulate: it selects a registry key. Task 2's tests forbid format holes in every fragment. |
| **A dashboard is silently stale.** | `connector_freshness` ships in Phase 0, before any other chart, and empty states distinguish not-connected / never-synced / no-activity. |
| **6 charts = 6 requests = 6 cold starts** on Render free. | One `/insights/dashboard` round trip. |
| **A big org's `GROUP BY` is slow.** | Two covering indexes matching the query exactly; the window is capped at 90 days by default. Count round trips, don't time them. |
| **GitHub review calls blow the rate limit.** | Moved off the page load entirely (D6): reviews are fetched during a sync that runs at most every 6h, the PR set is capped before reviews are fetched, truncation is marked, and the existing `_request` backoff is reused. |
| **A "by whom" chart starts on deploy day** and reads as if nobody worked before. | Every chart reports `first_fact_at` and renders "Measured since <date>". Counts backfill from `source_last_modified`; authors cannot and must not be invented (D5). |
| **Re-admitting GitHub to the tick re-opens "Unknown source type".** | It gets a separate facts-only branch; `UNSYNCABLE_PROVIDERS` stays, and a test asserts GitHub never produces an `ingestion_jobs` row. |
| **No frontend test infra exists.** | `tsc --noEmit` plus a written manual pass per frontend task (org member vs space member). Do **not** introduce a React test stack as a side effect of this feature — that is its own decision. |
| **The registry rots** as sources change. | It is small and admitted, like `app/llm/catalog.py`. Phase 1 adds a test that every registry entry's `provider` is one a connector actually supports. |

## Deliberately not built

- A chart builder / drag-and-drop / custom metrics — that is a BI product.
- Image or PNG generation. A picture of numbers cannot be filtered, re-scoped
  or clicked through to the PR, which is the entire request; and it is
  unverifiable, which this codebase does not accept anywhere else.
- A second chat surface. One section with a constrained ask box.
- A `base.py` for `app/insights/` — one implementation, no second backend.
- Vega-Lite, until Phase 3 lets the model choose chart shapes freely.
- Backfilling author names. The data was never captured; a chart may start
  late, but it may not contain a guess.

## On completion

Update `CLAUDE.md` §3 (a Visual Representation block: the invariant, the
registry, GitHub's live path, the scoping), §5 (whatever actually cost
debugging time), §6 (`activity_facts`, `insight_pins`) and §7 (built / gaps) —
one dense line each, not a narrative.
