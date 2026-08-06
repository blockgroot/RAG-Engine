# GitHub Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let an organization connect its GitHub organization to the platform once,
index each repo's prose docs into the existing RAG corpus, and answer
commit-level questions by fetching live from the GitHub API — behind a new
`GitHubAgent` that implements the *existing* `Agent` contract.

**Architecture:** A GitHub App installed on the customer's GitHub org (GitHub
itself enforces which repos are visible). **Nothing is embedded.** Every answer
comes from a live, bounded GitHub API tool-call — `get_readme`, `get_commit`,
`list_commits`, `list_repos` — decided by the LLM via real function-calling, in
the same shape as the Phase 5 web-search fallback. The existing RAG corpus,
confidence gate, grounded prompt, reranker, and tenant isolation are completely
untouched: GitHub adds no documents, no chunks, and no rows to the vector store.

---

## Revision log

**2026-08-05, revision 1 (after design review) — the ingestion half is cut.**
The original plan indexed `README` + `docs/**` into the chunk/embed/store path
(old decisions D5/D8/D9 and old Phases 4–5). That is **reversed**: v1 embeds
nothing at all. Reasoning in the revised D5 below. Net effect: two whole phases
(adapter + sync wiring) are deleted rather than deferred, and `GitHubAgent`
becomes a genuine tool-calling agent rather than a `RagPipelineAgent` subclass.
Phases 1–3 were already committed and are unaffected — they are the credential
layer, which both designs need identically.

**Tech Stack:** Python 3.12, FastAPI, Postgres + pgvector, `httpx`, `pyjwt` +
`cryptography` (both **already** dependencies — zero new Python deps), Next.js 15
frontend.

**Branch:** `feature/github-integration`, based on `main`.

---

## 1. Understanding summary

- **What.** GitHub as a third connectable source after Notion and Google Drive,
  but a structurally different one: an org admin clicks "Connect GitHub",
  installs our GitHub App on their GitHub organization, and gains repo Q&A —
  *"what does this service do"* and *"what happened in commit abc123"* — both
  answered by **live API calls**, not retrieval.
- **Why nothing is embedded.** Code plainly can't be: it isn't prose, it doesn't
  chunk or embed meaningfully, and doing it properly is a separate large feature
  (AST-aware chunking, code-specific embedding models). But the README doesn't
  need embedding *either* — and that's the sharper point. A README is small,
  changes rarely, and is trivially cheap to fetch fresh, so indexing it buys
  nothing while costing a whole ingestion/sync lifecycle **and** introducing a
  staleness window that live fetching cannot have (a policy doc can drift
  between edit and re-ingest; a live-fetched README has nothing to keep in
  sync). Once commits are already tool-calls, making the README a tool-call too
  is the consistent design rather than a second mechanism.
- **Who for.** Existing tenants' engineering users, via the existing chat UI.
- **New component.** `GitHubAgent` — the second real backend that
  `app/agent/base.py` was explicitly reserved for ("there genuinely is a second
  backend coming (GitHub), so the abstraction earns its keep").
- **Key constraint.** Tenant isolation must hold on **both** paths. The indexed
  path inherits it from `WHERE org_id = ...`. The live path is new risk: the
  `repo` argument is **LLM-filled**, so it must be validated against the
  installation's own repo list before any HTTP call. See §5 T1.
- **Non-goals (v1), each deliberate.** **Any embedding/ingestion of GitHub
  content** (revision 1); **workspace-scoped GitHub connections** — GitHub
  connects at the org level exactly like Notion and Drive, and any org member
  may ask about any authorized repo, because nothing else in this system has a
  scoping layer between org and individual member and inventing repo-level ACLs
  here would be a genuinely new access-control dimension built speculatively;
  AST/symbol chunking; issues; PRs; code search; GitHub Enterprise Server; write
  access; GitHub-based login/SSO.
- **Known functional limit of the no-embedding choice.** Without an index there
  is no semantic search *across* repos, so a vague question ("which service
  handles payments?") can't be resolved by similarity. Mitigation that makes
  this a non-issue in practice: `list_repos` returns each repo's **name,
  description, and topics** — GitHub already maintains those — which is enough
  signal for the model to pick the right repo before calling `get_readme`. A
  real fuzzy-semantic need (e.g. "find the commit that fixed the login bug") is
  the trigger to revisit indexing, not something to pre-build.

## 2. Decisions (with alternatives and why)

| # | Decision | Alternatives considered | Why this |
|---|---|---|---|
| D1 | **GitHub App** installed on the customer's GitHub org | OAuth App user token; fine-grained PAT pasted by admin | Only the App model natively means "connect once → all repos in this GitHub org", with **GitHub** enforcing the boundary — the same externally-enforced boundary CLAUDE.md §2 gives as the reason for per-org Notion secrets. An OAuth App's `repo` scope grants every private repo the *user* can reach, making scope something *our* code enforces (weaker). A PAT revives the manual-secret style Google deliberately dropped (D3). |
| D2 | Store the **user access token** in `access_token_encrypted`; mint **installation tokens on demand** | Store installation tokens and refresh them | `access_token_encrypted` is `NOT NULL`, and the user token is what proves *who* connected. Installation tokens last 1 h and can be minted any time from the private key + `installation_id`, so storing them buys nothing. Minting slots into the existing provider-agnostic `get_live_connection_token` seam (Google's D10) — every caller benefits with no call-site change. |
| D3 | `installation_id` lives in **`source_config`** JSONB | New column; reuse `external_workspace_id` | `set_connection_config`'s own docstring already anticipates this: *"a future GitHub/Slack adapter will need its own shape (a repo name, a channel list)"*. `external_workspace_id` holds the GitHub org login (human-readable in the admin UI), same as Google stores an email there. |
| D4 | **Verify `installation_id` server-side** via the user token, never trust the redirect | Trust the `installation_id` query param | GitHub's docs are explicit: *"bad actors can hit this URL with a spoofed `installation_id`"*, and recommend generating a user access token and checking the installation is associated with that user. Trusting it would let an attacker bind **someone else's** GitHub org to their tenant — a cross-tenant data-exfiltration hole. Implemented via `GET /user/installations`. |
| D5 | ~~Index prose files~~ → **REVERSED (revision 1): embed nothing. `get_readme` is a live tool-call like every other GitHub read.** | Index `README` + `docs/**`; index all files | The README needs no index: it's small, rarely changes, and fetching it fresh costs one API call while an index costs an adapter, a sync lifecycle, provider-partitioned diffing, *and* a staleness window that live fetch cannot have. Since commits were already going to be tool-calls, indexing the README would mean two mechanisms answering the same agent's questions. YAGNI on the ingestion half: build it only when a real fuzzy-semantic-search need appears. Consequence accepted and documented in §1. |
| D5b | Record the admin's **actual authorized repo scope** (`repository_selection` = `all` \| `selected`, plus the repo list) in `source_config` | Assume all org repos are in scope | "Connect GitHub" does **not** grant everything — the admin picks "All repositories" or a specific subset on GitHub's install screen. Storing what they actually chose mirrors how Drive stores the picked `folder_id`, keeps our view honest, and lets `list_repos` be answered without assuming. GitHub remains the enforcer either way; this is bookkeeping so the UI and prompts don't overstate scope. |
| D6 | Commits/code fetched **live** via a bounded tool-call | Embed commit messages; embed diffs; a multi-step agent loop | Commit history is unbounded and append-only: embedding it grows forever and is stale the moment it lands. The Phase 5 web-search fallback already proves the pattern here — real function-calling, **one** bounded call, distinct provenance label, graceful degradation to the fixed fallback. |
| D7 | JWT signing via **`pyjwt` + `cryptography`** | `PyGithub`; `githubkit`; shelling out | Both libraries are **already** in `requirements.txt` (`session.py` signs session JWTs with `pyjwt`; `security/crypto.py` uses `cryptography`), and RS256 needs exactly those two. **Zero new dependencies** — same reasoning as D9 in the Google plan and notion-client-over-llama-index in §2. |
| D8 | ~~`GitHubAgent` subclasses `RagPipelineAgent`~~ → **REVISED (revision 1): `GitHubAgent` implements `Agent` directly, as a tool-calling agent** | Subclass `RagPipelineAgent`; add GitHub tools to `PolicyAgent` | Once nothing is embedded (D5), there is no retrieval, so there is no `RagPipeline` to adapt — `PolicyAgent`/`WorkspaceAgent`'s "thin adapter over a pipeline" shape simply doesn't apply. This is the first agent that isn't a RAG agent, which is exactly what `app/agent/base.py` claimed the abstraction was for ("says nothing about retrieval, gates, or web search"). Adding tools to `PolicyAgent` would put repo tools into every policy prompt. |
| D9 | GitHub writes **no rows** to `documents`/`chunks` | Reuse provider-partitioned sync | Follows from D5: with nothing ingested, `source_provider = 'github'` is never written, so the Google-era sync partitioning is simply unused here rather than extended. Nothing in the existing isolation or incremental-sync behaviour changes. |

## 3. O1 — orchestrator routing (**DECIDED**, implemented in Phase 6)

**How does an org-level chat request reach `GitHubAgent`?**

The original answer, *"deterministic, by connected source"*, is right for a
workspace (which has exactly one connected source) but cannot disambiguate at
**org** scope, where Notion policies and GitHub are commonly both connected.

**Decided: an explicit source selector on the chat request** —
`{"agent": "policy" | "github"}`, default `"policy"`, surfaced as a "Policies |
Code" tab in the chat header. Fully deterministic, no LLM classify call, no added
latency, and it tells the user which corpus answered instead of guessing for
them. Rejected alternative: an aux-LLM intent classifier, which would put a
non-deterministic step in front of the tenant-scoped path — precisely what the
confidence gate's design philosophy avoids.

Two implementation notes worth carrying forward:

- **`workspace_id` outranks the requested agent.** A sub-workspace is a narrower
  data boundary than a source choice, so a workspace member asking inside their
  workspace is never served org-wide GitHub content instead. An unrecognized
  `agent` value falls through to `PolicyAgent`, never to GitHub.
- **`GitHubAgent` has no conversation memory in v1.** GitHub questions are
  answered standalone, so follow-ups ("and the commit before that?") are not
  resolved against history. `POST /chat/conversations` therefore **rejects**
  `agent="github"` with a 400 rather than returning a conversation id that would
  silently do nothing. Adding memory later means giving the agent a
  `ConversationStore` and a rewrite step — the same mechanism the RAG path
  already uses, not a new one.

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
| T4 | ~~Ingest volume~~ — **eliminated by revision 1.** Nothing is ingested, so there is no first-sync cost, no job-queue load, and no corpus growth from GitHub at all | n/a — this risk was a consequence of the indexing design that was cut |
| T5 | Rate limits: 5 000 req/hr per installation (scaling to 12 500; 15 000 on Enterprise Cloud) | Now comfortably sufficient: the steady state is 1–2 calls **per question** rather than a bulk sync. The repo list is read from stored `source_config`, not re-fetched per question. Retry with backoff on 429/5xx, honouring `Retry-After`. |
| T8 | **Latency is now on the critical path.** With no index, every answer waits on a live GitHub call plus a second LLM round-trip to compose from the tool result | Bounded per-call timeouts and one single-step tool round (never a loop), inside the Phase 19 request deadline. Accepted trade for always-current answers and zero staleness — but unlike the RAG path there is no cache to fall back on, so a slow GitHub means a slow answer. |
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

## Phase 4 — Record the admin's actual authorized repo scope → P2, P3

*Implements D5b. Small, and it makes every later phase honest about scope.*

**Files:**
- Modify: `app/auth/credentials.py` (a `get_installation_scope` helper)
- Create: `app/githublive/repos.py` (fetch + normalize the installation's repo list)
- Modify: `app/api/auth.py` (persist scope on connect)
- Test: `tests/test_github_repo_scope.py`

`GET /installation/repositories` (installation token, paginated) returns
`total_count`, `repository_selection` (`all` | `selected`), and the repos. Persist
into `source_config` alongside `installation_id`:

```json
{
  "installation_id": "4242",
  "account_login": "acme-inc",
  "repository_selection": "selected",
  "repos": [
    {"full_name": "acme-inc/payments-svc", "description": "Billing + invoicing", "topics": ["go"]},
    {"full_name": "acme-inc/handbook", "description": "Engineering handbook", "topics": []}
  ]
}
```

`description` and `topics` are stored deliberately: they are the signal that lets
the model resolve a vague question to a repo with **no embeddings** (see §1's
known-limit note). This is the no-index answer to repo resolution.

**Tests:**
1. `repository_selection: "all"` is recorded as `all`.
2. `repository_selection: "selected"` records exactly the returned repos — never
   an assumption that everything is in scope.
3. Pagination is followed (repo list spans two pages).
4. `description`/`topics` survive into the stored config.
5. A repo list is re-fetched (not stale) when scope is refreshed after a
   reconnect that changed the selection.

**Commit:** `feat(github): record the installation's authorized repo scope`

---

## Phase 5 — `app/githublive/`: the live read layer → P3, P4

*This is the whole data path now. No adapter, no ingestion, no vector store.*

**Files:**
- Create: `app/githublive/base.py` (`GitHubReader` ABC + result dataclasses)
- Create: `app/githublive/rest.py` (`RestGitHubReader`, plain `httpx`)
- Create: `app/githublive/factory.py` (`build_github_reader`)
- Create: `app/githublive/__init__.py`
- Modify: `app/config/settings.py` (`GitHubLiveSettings`)
- Test: `tests/test_github_live.py`

Interface + factory, per CLAUDE.md §3 conventions (this is a real capability with
a plausible second backend — a GraphQL reader — so it earns a `base.py`):

| Operation | Endpoint | Returns |
|---|---|---|
| `list_repos()` | from stored `source_config` (Phase 4), no call | name, description, topics |
| `get_readme(repo)` | `GET /repos/{repo}/readme`, `Accept: application/vnd.github.raw` | raw Markdown, truncated to a byte budget |
| `get_commit(repo, sha)` | `GET /repos/{repo}/commits/{sha}` | message, author, date, changed files, patches truncated to a byte budget |
| `list_commits(repo, path?, since?, limit)` | `GET /repos/{repo}/commits` | recent commit summaries |

**Non-negotiables (each gets a test):**

1. **T1 — `repo` is untrusted input.** It arrives LLM-filled. Every operation
   resolves it against this connection's own stored repo list and raises
   `SourceError` **before any HTTP call** on a miss. For `repository_selection:
   "all"`, validate the owner matches `account_login` so a fully-qualified
   foreign repo (`other-org/secrets`) is still refused. This is the live path's
   equivalent of `WHERE org_id = ...`; it is the single most important test here.
2. **T6 — never send an unbounded payload.** README and every patch are
   truncated to a configured byte budget with an explicit `[truncated]` marker so
   the model knows the evidence is partial. Never request a full diff.
3. **T5 — retry/backoff** on 429/5xx honouring `Retry-After`; bounded attempts.
4. 404 → a clear "not found or not accessible" `SourceError` (GitHub returns 404
   for both, exactly like Drive).
5. Every failure wrapped as `SourceError(..., cause=exc)`.

**Tests:** repo allowlist rejects a foreign repo pre-network (T1, both
`all` and `selected` modes); README fetched and truncated; commit summary +
files parsed; oversize patch truncated with marker; 429 retried then succeeds;
404 → `SourceError`; `list_commits` honours `path`/`limit`.

**Commit:** `feat(github): bounded live GitHub read layer with repo allowlist`

---

## Phase 6 — `GitHubAgent` → P5, **and O1 signed off**

*The first non-RAG agent. `Agent`'s abstraction finally earns its keep.*

**Files:**
- Create: `app/agent/github_agent.py`
- Modify: `app/agent/factory.py` (`build_github_agent`), `app/agent/__init__.py`
- Modify: `app/rag/prompts.py` (tool definitions + the GitHub answer prompt)
- Modify: `app/config/settings.py` (`GitHubAgentSettings`: fallback copy, budgets)
- Modify: `app/agent/base.py` (document `source="github"`)
- Test: `tests/test_github_agent.py`

`GitHubAgent.answer(question, org_id, conversation_id=None, workspace_id=None)`:

1. Load this org's GitHub connection scope (Phase 4). No connection → the fixed
   fallback, no LLM call.
2. One `generate_with_tools` call offering `get_readme` / `get_commit` /
   `list_commits`, with the repo list (name + description + topics) in the
   prompt so the model can resolve which repo without retrieval.
3. If it calls a tool: execute **one** bounded round (repo validated per T1),
   feed results back, compose the final answer. No multi-step agent loop —
   matching the Phase 5 web-search single-step decision.
4. If it calls nothing, or the call fails/times out: return the fixed fallback.
   **Never** answer a GitHub question from model world-knowledge.
5. **T2 — tool output is untrusted.** READMEs and commit messages are writable by
   any repo contributor, a far wider authorship surface than curated policy docs.
   Fence with `<<<UNTRUSTED_DOCUMENT_CONTENT>>>` and scrub via
   `app/security/untrusted.py`, exactly as retrieved chunks are.
6. Map to `AgentResponse` with `source="github"`, `grounded` true only when a
   tool actually supplied the evidence, and `citations` pointing at the GitHub
   URLs used.

**Grounding note.** The confidence gate does not apply here — there is no
similarity score to gate on. The equivalent guarantee is structural: an answer
is only produced from tool output, and no tool output means the fallback. That
substitution must be stated in the code, not left implicit.

`_select_agent` in `app/api/chat.py` gains its third branch per O1. It stays the
one place that decision is made.

**Tests:** no connection → fallback, zero LLM calls; a SHA question triggers
`get_commit` and the answer contains the message; a "what does X do" question
triggers `get_readme`; foreign repo refused (T1); injection text in a commit
message is scrubbed (T2); tool failure → fallback; model calls no tool →
fallback; `source == "github"`; existing policy/workspace routing unchanged.

**Commit:** `feat(github): GitHubAgent answering from live tool calls only`

---

## Phase 7 — API + frontend wiring → P6

**Files:**
- Modify: `app/api/deps.py` (`get_github_agent`), `app/api/chat.py`
- Modify: `app/api/admin.py` — GitHub needs **no** folder/scope config endpoint
  (its scope came from the install screen); the Drive-only guards must return a
  clean 400 for GitHub rather than 500
- Modify: `frontend/components/ConnectionCard.tsx` — add `github` to `available`
  (labels already exist); show the authorized repo scope ("all repositories" or
  the list) instead of a folder picker
- Modify: `frontend/app/chat/` — the source selector from O1
- Test: extend `tests/test_api_admin.py`, `tests/test_api_chat.py`

**Manual verification gate.** Connect a real GitHub org, then through the real
UI: ask "what does `<repo>` do?" (expect a README-grounded answer) and "what
happened in commit `<sha>`?" (expect a commit-grounded answer). Confirm in the
DB that **no** `documents`/`chunks` rows were created for GitHub — that is the
observable proof D5 was implemented as designed.

**Commit:** `feat(github): expose GitHubAgent through the API and portal`

---

## Phase 8 — Evaluation + documentation

- Golden cases in `evaluation/golden_set.py`: one README-answerable, one
  live-commit, one fallback (no connection), one **injection** case mirroring
  `injection-sabbatical` (T2). GitHub cases are **advisory** in CI like the web
  cases — they need live GitHub credentials CI won't have.
- `tests/test_isolation.py`: org A's GitHub connection must never be reachable
  from org B, and the live path must refuse a repo outside its own installation.
- Update `CLAUDE.md`: §2 (reasoning, incl. why GitHub embeds nothing and why
  that differs from Notion/Drive), §3 (`app/githublive/`, `app/auth/github_app.py`,
  `app/auth/github_oauth.py`, `app/agent/github_agent.py`), §4 (gotchas:
  spoofable `installation_id`; the `Accept: application/json` token-exchange
  trap; 300/3000-file diff limits; `repository_selection` is the admin's choice,
  not an assumption), §5 (**no new tables** — `source_config` carries everything),
  §6 (built vs pending, and the PEM secret).
- **Live walkthrough**, recorded honestly including anything that half-works.

**Commit:** `docs: record GitHub integration decisions and findings`

---

## What deliberately does NOT change

After revision 1 the answer is "even more than before": the entire RAG path is
untouched, because GitHub never enters it. The gate
(`RAG_SIMILARITY_THRESHOLD` 0.35), the strict grounded prompt and its three
response modes, hybrid search + RRF + reranking, contextual retrieval,
conversation memory and the incremental summary fold, retrieval reuse, query
normalization, and `org_id` isolation all keep working exactly as today on
Notion/Drive content.

GitHub adds: **no new tables, no `documents` rows, no `chunks` rows, no
embeddings, and no ingestion jobs.** It stores exactly one thing — an
`oauth_connections` row plus its `source_config` — and reads everything else
live. `PolicyAgent` and `WorkspaceAgent` are not modified at all; the only
shared code that changes is `_select_agent` gaining a third branch.

## Sources

- [About the setup URL](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url) — the spoofable-`installation_id` warning behind D4
- [Generating an installation access token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
- [Generating a JWT for a GitHub App](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app)
- [Generating a user access token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app)
- [REST endpoints for App installations](https://docs.github.com/en/rest/apps/installations)
- [REST endpoints for commits](https://docs.github.com/en/rest/commits/commits)
- [REST endpoints for repository contents](https://docs.github.com/en/rest/repos/contents)
- [Rate limits for GitHub Apps](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/rate-limits-for-github-apps)
