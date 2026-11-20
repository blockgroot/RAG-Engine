# Ask a friend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From a "No answer found" reply in web chat, send a colleague from the asker's space an AI summary of the conversation plus the question, by Slack DM or email.

**Architecture:** A new orchestrator package `app/askfriend/` (no `base.py`: one implementation) with three focused modules — `privacy.py` (does the conversation hold content the recipient can't open?), `summary.py` (prompt + model call), `deliver.py` (Slack DM, email) — and `service.py` tying them together. A new router `app/api/ask_friend.py` exposes people / check / send. The refused turn is marked in `conversation_turns.meta` (`ask_friend: true`) and, after sending, `ask_friend_sent`. No new table.

**Tech Stack:** FastAPI, psycopg (Postgres, staging DB in tests), httpx, Next.js/React.

**Spec:** `docs/superpowers/specs/2026-11-20-ask-a-friend-design.md`

## Global Constraints

- `org_id`, the asker and their `Viewer` come only from the session (`app/api/deps.py`: `get_session`, `viewer_for`). The question and conversation are read from the DB, never trusted from the client body.
- A recipient must be a member of the chat's scope: `list_workspace_members(ws)` for a space, `list_members(org_id)` for company chat; never the asker. Otherwise 403. A space chat requires `assert_member(ws, org_id, user_id)` for the asker.
- The turn must be the asker's own (`get_conversation_turns(conversation_id=, org_id=, user_id=, workspace_id=)`) and its `meta.ask_friend` must be `true`; otherwise 404.
- Private content: `needs_approval` is recomputed in `send`; if true and `approve_private` is not `true` → 409 `{"detail": "approval_required"}`.
- The summary prompt puts `UNTRUSTED_POLICY` before the fenced conversation and `UNTRUSTED_REMINDER` after it; registered in `tests/test_untrusted_policy.py::_built_prompts` as `"ask_friend_summary"`.
- The summary output: `strip_citations` (`app/rag/cite.py`), then `enforce_link_provenance(text, [conversation text], allowlist=())`.
- Model call: `limit_question(session, model)`, then `use_model(model, org_id=org_id)` (`app/llm/routed.py`), `charge_to(user_id, org_id)`, `build_llm_provider().generate(prompt, max_tokens=1500)`. Reasoning off is handled by the provider.
- Rate limits: router takes `limit_request`; email delivery takes `limit_email(f"user:{user_id}")`; a daily cap bucket `Bucket(f"askfriend:user:{user_id}", burst=ask_friend_per_day, per_minute=ask_friend_per_day/1440)` taken through `rate_limit._take`, with `ask_friend_per_day: int = 20` added to `RateLimitSettings` (`app/config/security.py`, env `RATE_LIMIT_ASK_FRIEND_PER_DAY`).
- Failures raise `ProviderError` subclasses (`app/core/exceptions.py`) with `cause=` and `raise … from`. Heavy imports inside functions. `from __future__ import annotations`. Docstrings say why.
- `app/askfriend/` imports nothing from `app/actions/` or `app/mcphost/`.
- Tests: run ONLY the new/changed test files (`pytest tests/test_ask_friend*.py tests/test_untrusted_policy.py -q`); they use the staging DB from `.env`. Never run the full suite.
- Commits on `feat/ask-a-friend` only; never push.

## Review Focus

- A conversation whose earlier turns cited a document that was since deleted → treat as private (needs approval), never as safe.
- A recipient with no Slack account in the connected team → Slack option unavailable for them; send with `via="slack"` returns 409 `slack_unavailable`, never a silent email fallback.
- A very long conversation → only the latest `MAX_TURNS = 12` turns, each answer cut to 2000 chars, and the prompt says older turns were left out.
- A reopened chat → the "Ask a friend" button and the "Sent to …" line both come from stored `meta`, not from the live `done` event.
- Sending twice for the same turn → allowed (a second colleague), each records its own entry in `ask_friend_sent` (list), each takes the daily bucket.

---

### Task 1: Mark refused turns and store the sent record

**Files:**
- Modify: `app/api/chat.py` (gap block ~1659 and the `done` payload ~1681-1720)
- Modify: `app/memory/conversations.py` (`_META_KEYS` line 53; new function)
- Modify: `frontend/lib/sse.ts` (`ChatDonePayload`)
- Test: `tests/test_ask_friend_meta.py`

**Interfaces:**
- Produces: `done` payload key `ask_friend: bool` (true exactly when `record_gap` ran); `"ask_friend"` and `"ask_friend_sent"` in `_META_KEYS`; `add_ask_friend_sent(*, conversation_id: str, org_id: str, user_id: str, question: str, entry: dict) -> bool` — appends `entry` to `meta.ask_friend_sent` (a JSON list) on the newest turn of that conversation whose `question` matches, only if the conversation belongs to `user_id` and `org_id`; returns False when no row matched.

- [ ] **Step 1: Write the failing tests**

```python
def test_meta_keeps_ask_friend_flags():
    meta = meta_for_storage({"source": "notion", "ask_friend": True})
    assert meta["ask_friend"] is True
    assert meta_for_history({"source": "notion", "ask_friend": True,
                             "ask_friend_sent": [{"to_name": "p@x.com", "via": "slack", "at": "t"}]}
                            )["ask_friend_sent"][0]["via"] == "slack"

def test_add_ask_friend_sent_appends_only_for_owner(org):  # staging DB, org fixture like tests/test_feedback.py
    # conversation with one turn (question "q?") owned by user A, meta {"source":"none","ask_friend":true}
    assert add_ask_friend_sent(conversation_id=cid, org_id=org, user_id=a, question="q?",
                               entry={"to_name": "b@x.com", "via": "email", "at": "t1"})
    assert add_ask_friend_sent(conversation_id=cid, org_id=org, user_id=a, question="q?",
                               entry={"to_name": "c@x.com", "via": "slack", "at": "t2"})
    assert not add_ask_friend_sent(conversation_id=cid, org_id=org, user_id=b, question="q?", entry={})
    # reading the turn back: meta.ask_friend_sent has 2 entries in order
```

- [ ] **Step 2: Run** `pytest tests/test_ask_friend_meta.py -q` — Expected: FAIL (ImportError / KeyError).
- [ ] **Step 3: Implement.** Add the two keys to `_META_KEYS`. `add_ask_friend_sent`: one `UPDATE conversation_turns t SET meta = jsonb_set(coalesce(t.meta,'{}'), '{ask_friend_sent}', coalesce(t.meta->'ask_friend_sent','[]') || %s::jsonb) FROM conversations c WHERE …` joining on `c.user_id`/`c.org_id`, newest `turn_index` with that question, `RETURNING 1`. In `chat.py`, set a local `gap_recorded = True` inside the existing gap `if`, and add `"ask_friend": gap_recorded` to the `done` payload (the attachment path at ~1192 does the same). Add `ask_friend?: boolean; ask_friend_sent?: {to_name: string; via: "slack" | "email"; at: string}[]` to the frontend payload/meta types.
- [ ] **Step 4: Run** `pytest tests/test_ask_friend_meta.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Ask a friend: mark refused turns, store sent record"`

### Task 2: Private-content check

**Files:**
- Create: `app/askfriend/__init__.py`, `app/askfriend/privacy.py`
- Test: `tests/test_ask_friend_privacy.py`

**Interfaces:**
- Consumes: `ConversationTurnRow` (`app/memory/conversations.py`: `.cited: list[dict]` with `document_id`, `.meta: dict | None`); `viewer_for_person(org_id, email) -> Viewer` (`app/sources/google_groups.py`); `visibility_predicate(alias, param=…)` / `viewer_clause` (`app/security/visibility.py`).
- Produces: `needs_approval(org_id: str, turns: list[ConversationTurnRow], recipient_email: str) -> bool`.

- [ ] **Step 1: Write the failing tests** (staging DB; insert documents with `doc_is_public` / `doc_viewers`)

```python
def test_public_citations_need_no_approval(org): assert not needs_approval(org, [turn(cited=[pub_doc])], "b@x.com")
def test_doc_recipient_cannot_open_needs_approval(org): assert needs_approval(org, [turn(cited=[private_doc_for_a])], "b@x.com")
def test_doc_recipient_can_open_is_fine(org): assert not needs_approval(org, [turn(cited=[private_doc_shared_with_b])], "b@x.com")
def test_deleted_cited_doc_needs_approval(org): assert needs_approval(org, [turn(cited=[{"n":1,"document_id": str(uuid4())}])], "b@x.com")
def test_attachment_needs_approval(org): assert needs_approval(org, [turn(meta={"source":"x","attachments":["f.pdf"]})], "b@x.com")
def test_live_read_needs_approval(org): assert needs_approval(org, [turn(meta={"source":"github","live_sources":[{"url":"https://github.com/o/r"}]})], "b@x.com")
def test_no_turns_no_approval(org): assert not needs_approval(org, [], "b@x.com")
```

- [ ] **Step 2: Run** `pytest tests/test_ask_friend_privacy.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement `needs_approval`.** True if any turn's meta has non-empty `attachments` or `live_sources`. Otherwise collect cited `document_id`s; if none → False; else one query `SELECT count(*) FROM documents d WHERE d.org_id = %s AND d.id = ANY(%s::uuid[])` plus `viewer_clause(viewer_for_person(org_id, recipient_email), "d")`; True when the count is less than the number of distinct ids (deleted or unreadable both count as private).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Ask a friend: private-content check"`

### Task 3: Summary prompt and generation

**Files:**
- Create: `app/askfriend/summary.py`
- Modify: `tests/test_untrusted_policy.py` (`_built_prompts`: add `"ask_friend_summary": build_summary_prompt([("q?", ATTACK)], "q?", left_out=0)`)
- Test: `tests/test_ask_friend_summary.py`

**Interfaces:**
- Produces: `MAX_TURNS = 12`, `MAX_ANSWER_CHARS = 2000`; `build_summary_prompt(turns: list[tuple[str, str]], question: str, *, left_out: int) -> str`; `summarize(turns: list[ConversationTurnRow], question: str, llm: LLMProvider) -> str` (bounds the turns, builds the prompt, calls `llm.generate(prompt, max_tokens=1500)`, then `strip_citations`, then `enforce_link_provenance(text, [conversation text], ())`; raises `ProviderError` on an empty reply).

- [ ] **Step 1: Write the failing tests** (fake LLM object with `generate(prompt, *, max_tokens=None)` recording the prompt)

```python
def test_prompt_fences_conversation_with_policy():
    p = build_summary_prompt([("what is X?", "I couldn't find that.")], "what is X?", left_out=0)
    assert p.index(UNTRUSTED_POLICY) < p.index("what is X?") < p.index(UNTRUSTED_REMINDER)

def test_prompt_says_older_turns_left_out():
    assert "3 earlier" in build_summary_prompt([("q", "a")], "q", left_out=3)

def test_summarize_bounds_turns_and_answers():
    turns = [row(f"q{i}", "a" * 5000) for i in range(20)]
    summarize(turns, "q19", fake)
    assert "q7" not in fake.prompt and "q8" in fake.prompt and "a" * 2001 not in fake.prompt

def test_summarize_strips_markers_and_unseen_links():
    fake.reply = "X is a repo [1]. See https://evil.example/x"
    out = summarize([row("q", "see https://github.com/o/r")], "q", fake)
    assert "[1]" not in out and "evil.example" not in out

def test_empty_reply_raises():
    fake.reply = "  "
    with pytest.raises(ProviderError): summarize([row("q", "a")], "q", fake)
```

- [ ] **Step 2: Run** `pytest tests/test_ask_friend_summary.py tests/test_untrusted_policy.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement.** Prompt instructions (exact intent, wording free): write to a colleague on the asker's behalf; a clear summary of what was asked and what Handbook found or could not find; medium length — about 3–5 sentences for a short chat, up to ~12 short lines or bullets for a long one; no invented facts; end with a line `Question: <question>` restated plainly; plain text, no markdown headings. The conversation goes inside the fence as `Q: … / A: …` pairs, answers cut at `MAX_ANSWER_CHARS`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Ask a friend: summary prompt"`

### Task 4: Delivery — Slack DM and email with Reply-To

**Files:**
- Create: `app/askfriend/deliver.py`
- Modify: `app/sources/slack_utils.py` (two helpers), `app/auth/email.py` (`reply_to` through `_dispatch` and the three backends)
- Test: `tests/test_ask_friend_deliver.py`

**Interfaces:**
- Produces:
  - `slack_utils.lookup_user_id(token: str, email: str) -> str | None` (`users.lookupByEmail`; never raises; None when not found).
  - `slack_utils.open_dm(token: str, user_id: str) -> str | None` (`conversations.open`; never raises).
  - `email._dispatch(..., reply_to: str | None = None)`: SMTP sets the `Reply-To` header; Resend `payload["reply_to"] = reply_to`; SendGrid `payload["reply_to"] = {"email": reply_to}`; console prints it.
  - `deliver.slack_token(org_id: str, workspace_id: str | None) -> str | None` — the space's Slack connection token, else the company's, else None (`get_live_connection_token`, catching `ProviderError`).
  - `deliver.compose(asker_name: str, summary: str, asker_mention: str | None) -> str` — exact copy: `"{asker_name}{' (' + asker_mention + ')' if mention else ''} asked Handbook something it couldn't answer and thought you might know.\n\n{summary}\n\n{reply_line}"` where `reply_line` is `"Reply to {asker_mention} directly."` on Slack, `"Just reply to this email to answer {asker_name}."` on email.
  - `deliver.send_slack(token: str, recipient_email: str, asker_email: str, asker_name: str, summary: str) -> None` — raises `DeliveryError(ProviderError)` when the recipient isn't found, the DM can't open, or the post fails.
  - `deliver.send_email(recipient_email: str, asker_email: str, asker_name: str, summary: str) -> None` — subject `"{asker_name} asked you a question via Handbook"`, `reply_to=asker_email`; wraps failures in `DeliveryError`.

- [ ] **Step 1: Write the failing tests** (monkeypatch `httpx.post`/`httpx.get` and `_dispatch`)

```python
def test_send_slack_dms_with_asker_mention(monkeypatch): # lookup returns U_B for recipient, U_A for asker
    send_slack("xoxb", "b@x.com", "a@x.com", "Rahul", "Summary text")
    assert posted["channel"] == "D123" and "(<@U_A>)" in posted["text"] and "Reply to <@U_A> directly." in posted["text"]

def test_send_slack_without_asker_in_slack_names_only(monkeypatch): # asker lookup None
    assert "Rahul asked Handbook" in posted["text"] and "<@" not in posted["text"]

def test_send_slack_recipient_missing_raises(monkeypatch):
    with pytest.raises(DeliveryError): send_slack("xoxb", "nobody@x.com", "a@x.com", "Rahul", "s")

def test_send_email_sets_reply_to(monkeypatch):
    send_email("b@x.com", "a@x.com", "Rahul", "s")
    assert sent["reply_to"] == "a@x.com" and sent["to"] == "b@x.com"

def test_sendgrid_payload_carries_reply_to(monkeypatch):  # _send_sendgrid with reply_to
    assert captured_json["reply_to"] == {"email": "a@x.com"}
```

- [ ] **Step 2: Run** `pytest tests/test_ask_friend_deliver.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** the helpers above; Slack helpers follow `post_message`'s style (httpx, `_API_BASE`, `_TIMEOUT`, log and return None on failure).
- [ ] **Step 4: Run** `pytest tests/test_ask_friend_deliver.py tests/test_email.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Ask a friend: Slack DM and email delivery"`

### Task 5: Service and API

**Files:**
- Create: `app/askfriend/service.py`, `app/api/ask_friend.py`
- Modify: `app/api/main.py` (include router), `app/config/security.py` (`ask_friend_per_day`), `app/security/rate_limit.py` (`limit_ask_friend(user_id: str) -> None`)
- Test: `tests/test_ask_friend_api.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces (routes, all `Depends(get_session)` + `limit_request`):
  - `GET /ask-friend/people?workspace_id=` → `{"people": [{"user_id", "email"}]}` (scope members minus the asker, sorted by email).
  - `POST /ask-friend/check` body `{conversation_id, question, recipient_user_id, workspace_id?}` → `{"needs_approval": bool, "slack_available": bool}`.
  - `POST /ask-friend/send` body `{conversation_id, question, recipient_user_id, via: "slack"|"email", approve_private: bool, model?: str, workspace_id?}` → `{"sent": {"to_name", "via", "at"}}`.
  - Errors: 403 recipient not in scope / asker not a space member; 404 turn not found or not `ask_friend`; 409 `approval_required`; 409 `slack_unavailable`; 429 from limits; 502 `{"detail": "delivery_failed"}` on `DeliveryError`.
- Order in `send`: validate scope and turn → `needs_approval` → `limit_ask_friend` → (email: `limit_email`) → `limit_question` → summarize → deliver → `add_ask_friend_sent`. `to_name` is the recipient's email (users have no display name); `at` is ISO UTC. Asker name = the part of their email before `@`, title-cased per dot-separated part (`rahul.verma` → `Rahul Verma`).

- [ ] **Step 1: Write the failing tests** (TestClient + session cookie as in `tests/test_api_github_admin.py`; monkeypatch `summary.summarize` and `deliver.send_slack/send_email`)

```python
def test_people_lists_scope_members_without_asker(client, org_two_users): ...  # == [b]
def test_send_to_non_member_is_403(client, ...): ...   # recipient from another space
def test_send_on_answered_turn_is_404(client, ...): ... # meta.ask_friend absent
def test_private_without_approval_is_409(client, ...): ...  # needs_approval patched True
def test_private_with_approval_sends_and_records(client, ...):
    r = client.post("/ask-friend/send", json={..., "via": "email", "approve_private": True}, cookies=c)
    assert r.status_code == 200 and sent_calls == 1
    # turn meta.ask_friend_sent[-1]["via"] == "email"
def test_slack_unavailable_is_409(client, ...): ...  # slack_token -> None
def test_delivery_failure_is_502_and_records_nothing(client, ...): ...
def test_daily_cap_refuses_21st(client, ..., monkeypatch): ...  # RATE_LIMIT_ASK_FRIEND_PER_DAY=1 → second send 429
```

- [ ] **Step 2: Run** `pytest tests/test_ask_friend_api.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** service functions `people(org_id, user_id, workspace_id) -> list[dict]`, `check(session, body) -> dict`, `send(session, body) -> dict` and the thin router. `check.slack_available` = `slack_token(...)` is not None and `lookup_user_id(token, recipient_email)` is not None.
- [ ] **Step 4: Run** `pytest tests/test_ask_friend_api.py -q` — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Ask a friend: API"`

### Task 6: Chat UI

**Files:**
- Create: `frontend/components/AskFriend.tsx`
- Modify: `frontend/components/ChatMessage.tsx` (hint block ~138-160), `frontend/lib/api.ts` (three calls), `frontend/app/globals.css`
- Test: manual in the running app (no frontend unit tests in this repo); `npm run build` must pass.

**Interfaces:**
- Consumes: Task 5 routes; `message.done?.ask_friend ?? message.meta?.ask_friend`; `message.meta?.ask_friend_sent` plus any sent this session.
- Produces: `api.askFriendPeople(workspaceId?)`, `api.askFriendCheck(body)`, `api.askFriendSend(body)`.

- [ ] **Step 1:** `AskFriend` renders a `chat-chart-hint` button "Ask a friend"; clicking opens an inline panel: a `<select>` of people (email), radio Slack/Email (Slack disabled with the note "Not in your Slack" when `slack_available` is false; default Slack when available), the approval checkbox with the exact copy `The summary includes information from private documents you can access that {email} can't open. I approve sending it.` shown only when `needs_approval`, and **Send** (disabled until a person is picked and, when needed, the box is ticked; shows "Sending…" while waiting). `check` runs on each person change. Errors show inline: 429 → the server message; 502 → "Couldn't send. Try again or pick email."
- [ ] **Step 2:** After success, and for each stored `ask_friend_sent` entry, show `Sent to {to_name} via {Slack|email} ✓` under the answer; the button stays for asking another person.
- [ ] **Step 3: Run** `cd frontend && npm run build` — Expected: build succeeds with no type errors.
- [ ] **Step 4: Verify manually** with the app running (`run` skill): ask an unanswerable question → button → send by email (console sender prints the email with Reply-To) → "Sent to …" appears and survives reopening the chat.
- [ ] **Step 5: Commit** `git commit -m "Ask a friend: chat button and panel"`

### Task 7: Rules and status docs

**Files:**
- Modify: `CLAUDE.md` (§3, after the conversation-is-personal bullet), `docs/handbook/PRODUCT_STATUS.md` (feature table + ship note)

- [ ] **Step 1:** Add to CLAUDE.md §3: `- **Ask a friend** (`app/askfriend/`): from a refused web-chat turn the asker sends an AI summary of their conversation to ONE member of the chat's scope they picked (never the model), by bot Slack DM or email with Reply-To. Content the recipient cannot open (a cited document outside their `Viewer`, an attachment, a live read) needs the asker's tick, re-checked on send (`privacy.needs_approval`). It is a notification, not an Act write; replies are not tracked.`
- [ ] **Step 2:** Add a PRODUCT_STATUS row: `| Ask a friend | On "No answer found", send a colleague in your space an AI summary + the question by Slack or email; private content needs a tick | Built 20 Nov (branch) | tests/test_ask_friend_*.py | Replies not tracked; web chat only |`.
- [ ] **Step 3: Commit** `git commit -m "Ask a friend: CLAUDE.md rule and status"`
