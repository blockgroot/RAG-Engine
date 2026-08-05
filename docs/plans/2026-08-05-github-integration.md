# GitHub Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let an organization connect its GitHub organization to the platform once,
index each repo's prose docs into the existing RAG corpus, and answer
commit-level questions by fetching live from the GitHub API — behind a new
`GitHubAgent` that implements the *existing* `Agent` contract.

**Architecture:** A GitHub App installed on the customer's GitHub org (GitHub
itself enforces which repos are visible). Prose files (`README`, `docs/**`) are
ingested through the **unchanged** `ingest_source()` → chunk → embed → store
path as ordinary `org_id`-scoped rows with `source_provider = 'github'`. Code and
commit history are **never embedded**; they are fetched at question time through
a bounded tool-call, reusing the exact shape of the Phase 5 web-search fallback.
The confidence gate, strict grounded prompt, reranker, and tenant isolation are
byte-for-byte unchanged.

**Tech Stack:** Python 3.12, FastAPI, Postgres + pgvector, `httpx`, `pyjwt` +
`cryptography` (both **already** dependencies — zero new Python deps), Next.js 15
frontend.

**Branch:** `feature/github-integration`, based on `main`.

---

## 1. Understanding summary

- **What.** GitHub as the third external source after Notion and Google Drive.
  An org admin clicks "Connect GitHub", installs our GitHub App on their GitHub
  organization, and gains repo Q&A: *"what does this service do"* (answered from
  indexed docs) and *"what happened in commit abc123"* (answered from a live API
  fetch).
- **Why two paths.** Prose docs are small, stable, and semantically searchable —
  they index well. Code and git history are large, volatile, and unbounded;
  embedding them would cost enormously, go stale immediately, and require
  AST-aware chunking we do not have. Fetching them live is cheaper *and* always
  current. This is the same reasoning CLAUDE.md §1 uses for RAG-over-fine-tuning,
  applied one level down.
- **Who for.** Existing tenants' engineering users, via the existing chat UI.
- **New component.** `GitHubAgent` — the second real backend that
  `app/agent/base.py` was explicitly reserved for ("there genuinely is a second
  backend coming (GitHub), so the abstraction earns its keep").
- **Key constraint.** Tenant isolation must hold on **both** paths. The indexed
  path inherits it from `WHERE org_id = ...`. The live path is new risk: the
  `repo` argument is **LLM-filled**, so it must be validated against the
  installation's own repo list before any HTTP call. See §5 T1.
- **Non-goals (v1), each deliberate.** Workspace-scoped GitHub connections
  (deferred by explicit request — the `workspace_id` column already exists, so
  passing `None` now costs nothing later); embedding source code; AST/symbol
  chunking; issues; PRs; code search; GitHub Enterprise Server; write access;
  GitHub-based login/SSO.

## 2. Decisions (with alternatives and why)

| # | Decision | Alternatives considered | Why this |
|---|---|---|---|
| D1 | **GitHub App** installed on the customer's GitHub org | OAuth App user token; fine-grained PAT pasted by admin | Only the App model natively means "connect once → all repos in this GitHub org", with **GitHub** enforcing the boundary — the same externally-enforced boundary CLAUDE.md §2 gives as the reason for per-org Notion secrets. An OAuth App's `repo` scope grants every private repo the *user* can reach, making scope something *our* code enforces (weaker). A PAT revives the manual-secret style Google deliberately dropped (D3). |
| D2 | Store the **user access token** in `access_token_encrypted`; mint **installation tokens on demand** | Store installation tokens and refresh them | `access_token_encrypted` is `NOT NULL`, and the user token is what proves *who* connected. Installation tokens last 1 h and can be minted any time from the private key + `installation_id`, so storing them buys nothing. Minting slots into the existing provider-agnostic `get_live_connection_token` seam (Google's D10) — every caller benefits with no call-site change. |
| D3 | `installation_id` lives in **`source_config`** JSONB | New column; reuse `external_workspace_id` | `set_connection_config`'s own docstring already anticipates this: *"a future GitHub/Slack adapter will need its own shape (a repo name, a channel list)"*. `external_workspace_id` holds the GitHub org login (human-readable in the admin UI), same as Google stores an email there. |
| D4 | **Verify `installation_id` server-side** via the user token, never trust the redirect | Trust the `installation_id` query param | GitHub's docs are explicit: *"bad actors can hit this URL with a spoofed `installation_id`"*, and recommend generating a user access token and checking the installation is associated with that user. Trusting it would let an attacker bind **someone else's** GitHub org to their tenant — a cross-tenant data-exfiltration hole. Implemented via `GET /user/installations`. |
| D5 | Index **prose files only**: `README*` + `docs/**` (`.md`/`.mdx`/`.rst`/`.txt`), with per-repo file and per-file size caps | Index all files; index nothing | Answers "what does this repo do" — the actual ask — while keeping the corpus bounded across an org with hundreds of repos. Mirrors Google's "native Docs only" (D5) and Notion's page filtering. |
| D6 | Commits/code fetched **live** via a bounded tool-call | Embed commit messages; embed diffs; a multi-step agent loop | Commit history is unbounded and append-only: embedding it grows forever and is stale the moment it lands. The Phase 5 web-search fallback already proves the pattern here — real function-calling, **one** bounded call, distinct provenance label, graceful degradation to the fixed fallback. |
| D7 | JWT signing via **`pyjwt` + `cryptography`** | `PyGithub`; `githubkit`; shelling out | Both libraries are **already** in `requirements.txt` (`session.py` signs session JWTs with `pyjwt`; `security/crypto.py` uses `cryptography`), and RS256 needs exactly those two. **Zero new dependencies** — same reasoning as D9 in the Google plan and notion-client-over-llama-index in §2. |
| D8 | `GitHubAgent` **subclasses `RagPipelineAgent`** | A from-scratch `Agent`; add GitHub tools to `PolicyAgent` | `WorkspaceAgent` already proves the pattern: a distinct agent is just a different pipeline (prompt profile + fallback copy). Writing a fresh agent would duplicate the gate/prompt/memory logic that CLAUDE.md insists lives in exactly one place. Adding tools to `PolicyAgent` would put repo tools in every policy prompt. |
| D9 | Provider-partitioned sync is **already done** — reuse it | Add a partition mechanism | `documents.source_provider` + `(org_id, source_provider, source_external_id)` landed with Google. GitHub inherits coexistence for free; the regression tests in `tests/test_incremental_sync.py` already cover the shape. |

## 3. Open decision needing your sign-off (blocks Phase 6)

**O1 — how does an org-level chat request reach `GitHubAgent`?**

You chose *"deterministic, by connected source"*, and that is right for a
workspace (a workspace has exactly one connected source). But **v1 is org-level
only**, and an org will commonly have *both* Notion policies **and** GitHub
connected. At org scope, "by connected source" does not disambiguate — so
`_select_agent` needs one more input.

Recommendation: **an explicit source selector on the chat request**
(`{"agent": "policy" | "github"}`, default `"policy"`), surfaced as a small tab
in the chat header. It stays fully deterministic, needs no LLM classify call, is
one new field, and is honest to the user about which corpus answered. The
alternative (an aux-LLM intent classifier) adds a non-deterministic step in front
of the tenant-scoped path and latency to every request — exactly what the
confidence gate's design philosophy avoids.

Phases 1–5 do **not** depend on O1 and can be built while it's open.

## 4. The connect flow, end to end (answers your question 3)

This is what actually happens after the admin clicks "Connect GitHub":

```
1. Admin clicks "Connect GitHub" in the Sources page.
      → GET /auth/github/authorize      (existing route, existing session auth)
      → create_state(org_id, "github")  (existing single-use server-side state)
      → 302 to https://github.com/apps/<APP_SLUG>/installations/new?state=<state>

2. GitHub shows ITS OWN install screen. The admin picks the GitHub
   organization and chooses "All repositories" (or a subset).
   >>> This screen is where repository access is granted. We never ask for
   >>> repo permissions ourselves — GitHub owns that UI and that boundary. <<<

3. GitHub redirects back:
      → GET /auth/github/callback?code=...&installation_id=...&setup_action=install&state=...

4. Our callback (all server-side, nothing trusted from the client):
      a. consume_state(state, provider="github")        -> org_id   (single-use)
      b. exchange_code(code)                            -> USER access token
      c. GET /user/installations with that user token
         -> assert the returned installation_id matches the query param   [D4]
         -> take account.login / account.id from the VERIFIED record
      d. save_connection(org_id, "github", tokens)      (user token, encrypted)
      e. set_connection_config(org_id, "github", {installation_id, account_login})
      → 302 /onboarding?connected=github

5. Any later content call needs a token:
      get_live_connection_token(org_id, "github")
      → read installation_id from source_config
      → sign a 10-min RS256 JWT (iss = App client id, iat 60s in the past)
      → POST /app/installations/<id>/access_tokens
      → 1-hour installation token, cached in-process until ~5 min before expiry
```

Two things worth internalising: **step 2 is why D1 was chosen** — repo access is
granted in GitHub's own UI and enforced by GitHub, not by a field in our
database. And **step 4c is not optional** — without it the flow has the
cross-tenant hole described in D4.

## 5. Risks

| # | Risk | Mitigation |
|---|---|---|
| T1 | **LLM-filled `repo` argument** could name a repo outside the installation (or another tenant's repo) | Every live call resolves `repo` against the cached `GET /installation/repositories` list for *this* connection and raises before any HTTP call on a miss. This is the live path's equivalent of `WHERE org_id = ...` and must be tested explicitly. |
| T2 | **READMEs and commit messages are attacker-writable.** Any repo contributor can commit prompt-injection text — a materially wider authorship surface than curated HR policy docs | Both paths go through the Phase 16 defences: fence with `<<<UNTRUSTED_DOCUMENT_CONTENT>>>` and scrub via `app/security/untrusted.py`. Add golden injection cases mirroring `injection-sabbatical`. CLAUDE.md is honest that this is partial mitigation, not a solution. |
| T3 | `state` round-tripping through the *install* URL (vs the plain authorize URL) is **not confirmed by GitHub's docs** — the setup-URL page documents `installation_id` but not `state` | Verified during Phase 2 against a real App before building on it. Fallback if `state` does not survive: register the App's **Setup URL** as a dedicated `/auth/github/setup` route and resolve the org from the authenticated session instead of state. Flagged the same way the Google plan flagged R2 rather than assuming. |
| T4 | Ingest volume: an org with 500 repos × many docs could be a very large first sync | Per-repo file cap and per-file byte cap (D5), enforced in the adapter. The durable Phase 12 job queue already means a long sync doesn't block the API. |
| T5 | Rate limits: 5 000 req/hr per installation (scaling to 12 500; 15 000 on Enterprise Cloud) | Ingest is metadata-first via one recursive tree call per repo rather than per-file listing; live lookups are one call per question. Retry with backoff on 429/5xx, honouring `Retry-After`. |
| T6 | A commit diff can be enormous (300 files per page, up to 3 000; "larger diffs may time out") | Never request full diffs for context. Fetch the commit summary + changed-file list, and truncate patches to a configured byte budget before they reach the prompt. |
| T7 | Private key (PEM) is **new secret material** the current secrets story doesn't cover | Read only via `GitHubSettings.from_env()` (`GITHUB_APP_PRIVATE_KEY`, supporting a `\n`-escaped single-line value); never logged. Added to the deployment-secrets list in CLAUDE.md §6 alongside `AUTH_JWT_SECRET` / `AUTH_ENCRYPTION_KEYS`. |

---

# Implementation phases

Each phase: small, independently testable, own commit(s), **full suite green
before the next**. `→` marks dependencies.

Run the suite with `pytest -q -m "not network"` unless a task says otherwise.

---

## Phase 1 — `GitHubSettings` + installation-token minting

*Independent. Pure functions and HTTP fakes only — no DB, no live GitHub.*

**Files:**
- Modify: `app/config/settings.py` (add `GitHubSettings` beside `GoogleSettings`)
- Create: `app/auth/github_app.py`
- Create: `tests/test_github_app.py`

**Step 1: Write the failing test for JWT shape**

```python
# tests/test_github_app.py
import time
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

from app.auth.github_app import build_app_jwt


def _pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def test_app_jwt_is_rs256_with_backdated_iat_and_bounded_exp():
    token = build_app_jwt(client_id="Iv1.abc", private_key_pem=_pem())

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256"

    claims = jwt.decode(token, options={"verify_signature": False})
    now = int(time.time())
    assert claims["iss"] == "Iv1.abc"
    assert claims["iat"] <= now - 55          # backdated ~60s for clock drift
    assert claims["exp"] - claims["iat"] <= 600  # GitHub's hard 10-minute cap
```

**Step 2: Run it to verify it fails**

Run: `pytest tests/test_github_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.auth.github_app'`

**Step 3: Add `GitHubSettings`**

In `app/config/settings.py`, beside `GoogleSettings`:

```python
@dataclass(frozen=True)
class GitHubSettings:
    """Configuration for the GitHub App "Connect" flow.

    GitHub is App-only (plan decision D1): there is no env-var token path, for
    the same reason Google has none. ``private_key`` is new secret material —
    an RS256 PEM used only to sign the short-lived App JWT that mints
    installation tokens; it is never logged and never leaves this process.
    """

    app_slug: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    private_key: str | None = None

    @classmethod
    def from_env(cls) -> "GitHubSettings":
        raw_key = os.getenv("GITHUB_APP_PRIVATE_KEY")
        return cls(
            app_slug=os.getenv("GITHUB_APP_SLUG"),
            client_id=os.getenv("GITHUB_CLIENT_ID"),
            client_secret=os.getenv("GITHUB_CLIENT_SECRET"),
            # Accept a \n-escaped single-line value so the PEM survives .env
            # files and secret managers that can't hold real newlines.
            private_key=raw_key.replace("\\n", "\n") if raw_key else None,
        )
```

**Step 4: Implement `build_app_jwt` + `mint_installation_token`**

```python
# app/auth/github_app.py
"""GitHub App authentication primitives (plan Phase 1).

Two things live here, both pure-ish and independent of the DB:

1. ``build_app_jwt`` — signs the short-lived RS256 JWT that authenticates us as
   the *App itself*. GitHub caps ``exp`` at 10 minutes and recommends
   backdating ``iat`` 60s against clock drift; both are enforced here rather
   than left to callers.
2. ``mint_installation_token`` — exchanges that JWT for a 1-hour *installation*
   access token, which is what actually reads repo content.

Why no stored installation token: it expires in an hour and can be re-minted
any time from the private key + installation id, so persisting it would add a
refresh lifecycle for no benefit (plan decision D2).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
import jwt

from ..config.settings import GitHubSettings
from ..core.exceptions import ConfigurationError, OAuthError

_API_BASE = "https://api.github.com"
_API_VERSION = "2022-11-28"
_JWT_TTL_SECONDS = 540      # 9 min — inside GitHub's 10-minute ceiling
_IAT_BACKDATE_SECONDS = 60  # GitHub's own clock-drift recommendation
_TIMEOUT = 15.0


def github_headers(token: str, *, accept: str = "application/vnd.github+json") -> dict[str, str]:
    """Standard GitHub REST headers. Pinning the API version is required."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": accept,
        "X-GitHub-Api-Version": _API_VERSION,
    }


def build_app_jwt(*, client_id: str, private_key_pem: str) -> str:
    if not client_id or not private_key_pem:
        raise ConfigurationError(
            "GitHub App JWT requires GITHUB_CLIENT_ID and GITHUB_APP_PRIVATE_KEY."
        )
    now = int(time.time())
    return jwt.encode(
        {"iat": now - _IAT_BACKDATE_SECONDS, "exp": now + _JWT_TTL_SECONDS, "iss": client_id},
        private_key_pem,
        algorithm="RS256",
    )


@dataclass(frozen=True)
class InstallationToken:
    token: str
    expires_at: datetime | None


def mint_installation_token(
    installation_id: str, settings: GitHubSettings | None = None
) -> InstallationToken:
    """Exchange an App JWT for a 1-hour installation access token."""
    settings = settings or GitHubSettings.from_env()
    app_jwt = build_app_jwt(
        client_id=settings.client_id, private_key_pem=settings.private_key
    )
    try:
        response = httpx.post(
            f"{_API_BASE}/app/installations/{installation_id}/access_tokens",
            headers=github_headers(app_jwt),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OAuthError(
            f"GitHub installation-token request failed: {exc}", cause=exc
        ) from exc

    data = response.json()
    token = data.get("token")
    if not token:
        raise OAuthError("GitHub installation-token response missing 'token'")
    return InstallationToken(token=token, expires_at=_parse_expiry(data.get("expires_at")))


def _parse_expiry(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
```

**Step 5: Run tests to verify they pass**

Run: `pytest tests/test_github_app.py -v`
Expected: PASS

**Step 6: Add the minting test**

```python
def test_mint_installation_token_posts_jwt_and_returns_token(monkeypatch):
    seen = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): return None
        def json(self):
            return {"token": "ghs_live", "expires_at": "2026-08-05T12:00:00Z"}

    def _fake_post(url, headers=None, timeout=None):
        seen["url"], seen["auth"] = url, headers["Authorization"]
        return _Resp()

    monkeypatch.setattr("app.auth.github_app.httpx.post", _fake_post)
    settings = GitHubSettings(client_id="Iv1.abc", private_key=_pem())

    result = mint_installation_token("12345", settings)

    assert result.token == "ghs_live"
    assert seen["url"].endswith("/app/installations/12345/access_tokens")
    # The App JWT authenticates this call — never an installation token.
    claims = jwt.decode(seen["auth"].removeprefix("Bearer "), options={"verify_signature": False})
    assert claims["iss"] == "Iv1.abc"
```

Run: `pytest tests/test_github_app.py -v` → PASS

**Step 7: Commit**

```bash
git checkout -b feature/github-integration
git add app/config/settings.py app/auth/github_app.py tests/test_github_app.py
git commit -m "feat(github): GitHubSettings + App JWT and installation-token minting"
```

---

## Phase 2 — `GitHubAppProvider` (the connect flow) → P1

**Files:**
- Create: `app/auth/github_oauth.py`
- Modify: `app/auth/factory.py` (add the `github` branch; update the error string)
- Modify: `app/auth/__init__.py` (export)
- Modify: `app/api/auth.py` (`callback` must persist `installation_id`)
- Modify: `tests/test_auth.py:66` — it currently asserts `build_oauth_provider("github")` **raises**; that assertion must flip (same as Google flipped `test_auth.py:61`)
- Create: `tests/test_github_oauth.py`

**Key implementation notes:**

- `authorize_url(state)` returns
  `https://github.com/apps/<app_slug>/installations/new?state=<state>` — the
  **install** page, not `/login/oauth/authorize`, because installation is what
  grants repo access (§4 step 2).
- `exchange_code(code)` POSTs form-encoded to
  `https://github.com/login/oauth/access_token` with
  `Accept: application/json` (GitHub returns form-encoded otherwise — a classic
  trap), yielding the **user** token.
- **`_verify_installation(user_token, installation_id)`** — `GET /user/installations`,
  find the matching id, return `account.login`. **Raise `OAuthError` if absent.**
  This is D4 and is the security core of the phase.
- `OAuthTokens.external_workspace_id` = the verified `account.login`.
- Carry the verified `installation_id` out of the provider so the callback can
  write it to `source_config`. Cleanest minimal shape: a
  `GitHubAppProvider.exchange_code_with_installation(code, installation_id)`
  returning `(OAuthTokens, installation_id)`, with `exchange_code` raising a
  clear error if called without an installation id — GitHub's flow genuinely
  needs that extra parameter the other two providers don't.

**Tests (all offline, monkeypatching `app.auth.github_oauth.httpx`):**

1. `authorize_url` contains the app slug and the state, and points at
   `/installations/new`.
2. `exchange_code` sends `Accept: application/json` and returns the user token.
3. **`installation_id` not in `GET /user/installations` → `OAuthError`** (the D4
   spoofing defence — the single most important test in this phase).
4. A *different* user's installation id → `OAuthError`.
5. `external_workspace_id` comes from the API response, never the query param.
6. Missing `GITHUB_APP_SLUG`/`CLIENT_ID`/private key → `ConfigurationError`.

**Verify T3 before finishing this phase:** register a real GitHub App, run the
install flow once, and confirm the callback actually receives `state` alongside
`installation_id` and `setup_action`. If it does not, switch to the Setup-URL
fallback in §5 T3 *now* rather than building Phases 3–7 on a false assumption.

**Commit:** `feat(github): GitHub App connect flow with verified installation id`

---

## Phase 3 — `get_live_connection_token` learns GitHub → P1, P2

**Files:**
- Modify: `app/auth/credentials.py`
- Create/extend: `tests/test_token_refresh.py`

For `provider == "github"`, ignore the stored user token for content access:
read `installation_id` from `source_config`, mint an installation token, and
cache it in-process keyed by `(org_id, workspace_id, installation_id)` until
5 minutes before expiry.

**Tests:**
1. A GitHub connection mints an installation token rather than returning the
   stored user token.
2. A second call inside the validity window does **not** hit the network (cache).
3. A cached token past its expiry margin is re-minted.
4. A GitHub connection with **no `installation_id`** in `source_config` raises
   `ConfigurationError` with an actionable "reconnect GitHub" message.
5. Notion/Google behaviour is **unchanged** (regression).

**Commit:** `feat(github): mint installation tokens through get_live_connection_token`

---

## Phase 4 — `GitHubAdapter`: index prose docs → P1, P3

*The largest phase. Answers your question 4: this is exactly what gets embedded.*

**Files:**
- Create: `app/sources/github.py`
- Modify: `app/sources/factory.py`, `app/sources/__init__.py`
- Create: `tests/test_github_source.py`

**What is embedded, precisely:**

| Included | Excluded |
|---|---|
| `README*` at repo root | every source-code file |
| `docs/**` with `.md`, `.mdx`, `.rst`, `.txt` | commits, diffs, branches, tags |
| `CONTRIBUTING.md`, `ARCHITECTURE.md` at root | issues, PRs, wikis, releases |
| — | binaries, images, lockfiles |
| — | archived + disabled repos, empty repos |

**Interface mapping:**

- `list_documents()` — `GET /installation/repositories` (paginated) for the repo
  list, then per repo **one** `GET /repos/{owner}/{repo}/git/trees/{default_branch}?recursive=1`
  to enumerate paths in a single call (T5), filtered by the table above. Honour
  the `truncated` flag by logging rather than silently under-ingesting (the same
  discipline as Drive's `incompleteSearch`). Cap files per repo.
- `external_id` = `f"{owner}/{repo}:{path}"` — stable, human-readable, and
  unique within `(org_id, 'github')`.
- `title` = `f"{repo} — {path}"` so the **repo name is in the chunk text**. This
  is what makes repo resolution work (your chosen approach) — retrieval surfaces
  the right repo, and the live tool's `repo` argument is filled from it.
- `fetch_document()` — `GET /repos/{owner}/{repo}/contents/{path}` with
  `Accept: application/vnd.github.raw+json`. Skip anything over the per-file cap
  (well under GitHub's 1 MB full-support threshold).
- `get_last_modified()` — `GET /repos/{owner}/{repo}/commits?path=<path>&per_page=1`,
  the commit date of the last change to that file. This is what makes incremental
  sync work: an edited doc gets a newer date and is re-ingested; untouched docs
  are acknowledged, not re-embedded.
- Retry/backoff helper on 429/5xx honouring `Retry-After`; wrap every failure as
  `SourceError(..., cause=exc)`; 404 → treat as inaccessible/removed (as Drive does).

**Tests (offline, monkeypatching the adapter module's `httpx`):**
1. Repo listing paginates.
2. Tree walk picks up `README.md` and `docs/a/b.md`, and **excludes** `src/main.py`.
3. Archived repo skipped.
4. `title` contains the repo name.
5. Raw content fetch returns a `SourceDocument`.
6. Oversize file skipped, not fatal.
7. 429 then success (retry works).
8. 404 mid-walk skips that repo and continues.
9. `truncated: true` is logged, not swallowed.
10. Full `ingest_source` round trip with `provider="github"`, plus **a Notion doc
    in the same org surviving a GitHub sync** (extend `tests/test_incremental_sync.py`).

**Commit:** `feat(github): GitHubAdapter indexing README and docs prose`

---

## Phase 5 — Wire connect + sync end to end → P2, P3, P4

Mostly configuration; the generic layers already handle a third provider.

**Files:**
- Modify: `app/sources/factory.py` (`elif source_type == "github"`)
- Modify: `app/api/admin.py` — the Drive-only guards on
  `/connections/{id}/config` and `/drive-folders` must return a clean 400 for
  GitHub, not 500; GitHub needs no folder config at all (its scope came from
  the install screen)
- Modify: `frontend/components/ConnectionCard.tsx` — add `github` to `available`
  (the labels already exist); GitHub shows **no** folder-config UI
- Test: extend `tests/test_api_admin.py`

**Manual verification gate — do not skip.** Connect a real GitHub org, run one
ingest through the worker, and confirm in `psql` that chunks exist with
`source_provider = 'github'` and the correct `org_id`. Then ask, through the
existing chat, *"what does <repo> do?"* — this must already answer from the
indexed README **before** any agent work starts. If it doesn't, the problem is
here, not in Phase 6.

**Commit:** `feat(github): wire GitHub connect and ingestion end to end`

---

## Phase 6 — `GitHubAgent` + orchestrator routing → P5, **and O1 signed off**

**Files:**
- Create: `app/agent/github_agent.py`
- Modify: `app/agent/factory.py` (`build_github_agent`), `app/agent/__init__.py`
- Modify: `app/rag/prompts.py` (`GITHUB_PROMPT_PROFILE`)
- Modify: `app/config/settings.py` (`GitHubAgentSettings` — its own fallback copy)
- Modify: `app/api/deps.py` (`get_github_agent`), `app/api/chat.py` (`_select_agent`)
- Modify: `app/agent/base.py` — document `source="github"` in the `AgentResponse` docstring
- Create: `tests/test_github_agent.py`

`GitHubAgent` subclasses `RagPipelineAgent` exactly as `WorkspaceAgent` does —
a different prompt persona and fallback string, web search off. **No answering
logic** (CLAUDE.md: "`PolicyAgent` must not add behavior — it's an adapter").

`_select_agent` gains its third branch per O1. Keep it the single place the
decision is made — that property is called out in `chat.py`'s own docstring.

**Tests:** routing picks GitHubAgent for the GitHub selector and PolicyAgent by
default; a GitHub question with no matching chunks returns the fallback with
`grounded=False`; `source == "github"`; existing policy/workspace routing tests
still pass unchanged.

**Commit:** `feat(github): GitHubAgent behind the existing Agent contract`

---

## Phase 7 — Live commit lookup → P6

*Answers your question 5: yes, there is a live API, and this is how it's wired.*

**Files:**
- Create: `app/githublive/base.py`, `rest.py`, `factory.py`
- Modify: `app/rag/prompts.py` (tool description + answer prompt)
- Modify: `app/rag/pipeline.py` (offer the tool where `web_search` is offered today)
- Modify: `app/config/settings.py` (`GitHubLiveSettings`: enabled, timeout, patch byte budget)
- Create: `tests/test_github_live.py`

**The two operations (v1):**

| Tool op | Endpoint | Returned to the model |
|---|---|---|
| `get_commit(repo, sha)` | `GET /repos/{owner}/{repo}/commits/{sha}` | message, author, date, changed-file list with add/del counts, patches truncated to the byte budget (T6) |
| `list_commits(repo, path?, since?)` | `GET /repos/{owner}/{repo}/commits` | recent commit summaries (sha, message first line, author, date) |

**Non-negotiables in this phase:**

1. **T1 — validate `repo` against the installation's repo list before any HTTP
   call.** The model filled that string; it is untrusted input. Test that a
   foreign repo raises and never reaches the network.
2. **T2 — commit messages and patches are untrusted.** Fence and scrub them
   exactly as retrieved chunks are, via `app/security/untrusted.py`.
3. **T6 — never send a full diff.** Truncate to the configured budget with an
   explicit "[truncated]" marker so the model knows the evidence is partial.
4. **Degrade like web search.** Any failure/timeout → the fixed fallback, never
   a crash and never an ungrounded guess.
5. **One call per question.** No multi-step agent loop, matching the Phase 5
   single-step decision.

**Tests:** SHA question triggers `get_commit` and the answer contains the commit
message; foreign repo → raises pre-network (T1); injection text in a commit
message is scrubbed (T2); oversize patch truncated (T6); API failure → fallback;
a docs question does **not** trigger a live call.

**Commit:** `feat(github): bounded live commit lookup via tool-calling`

---

## Phase 8 — Evaluation + documentation

- Add golden cases to `evaluation/golden_set.py`: two answerable-from-README,
  one live-commit, one fallback, one **injection** case mirroring
  `injection-sabbatical` (T2).
- Extend `tests/test_isolation.py`: org A must never retrieve org B's GitHub
  chunks, and the live path must refuse a repo outside its own installation.
- Update `CLAUDE.md` §2 (reasoning), §3 (`app/githublive/`, `app/sources/github.py`,
  `app/agent/github_agent.py`), §4 (gotchas: spoofable `installation_id`; the
  `Accept: application/json` token-exchange trap; the 300/3000-file diff limits),
  §5 (no new tables — `source_config` carries `installation_id`), §6 (built vs
  pending, and the PEM secret).
- **Live walkthrough:** connect → install → sync → ask "what does this repo do"
  → ask "what happened in commit `<sha>`" → edit a README → change-check →
  re-sync. Record results honestly, including anything that only half-works.

**Commit:** `docs: record GitHub integration decisions and findings`

---

## What deliberately does NOT change

The gate (`RAG_SIMILARITY_THRESHOLD` 0.35), the strict grounded prompt and its
three response modes, hybrid search + RRF + reranking, conversation memory and
the incremental summary fold, retrieval reuse, query normalization, and
`org_id` isolation. GitHub chunks are ordinary org-scoped rows; the live lookup
is one more tool in the slot web search already occupies. **No new tables.**

## Sources

- [About the setup URL](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url) — the spoofable-`installation_id` warning behind D4
- [Generating an installation access token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
- [Generating a JWT for a GitHub App](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app)
- [Generating a user access token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app)
- [REST endpoints for App installations](https://docs.github.com/en/rest/apps/installations)
- [REST endpoints for commits](https://docs.github.com/en/rest/commits/commits)
- [REST endpoints for repository contents](https://docs.github.com/en/rest/repos/contents)
- [Rate limits for GitHub Apps](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/rate-limits-for-github-apps)
