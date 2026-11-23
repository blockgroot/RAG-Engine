# Ask a friend — design

Date: 20 Nov 2026 · Branch: `feat/ask-a-friend` · Status: approved 20 Nov 2026

## Why

When Handbook finds no answer, the asker is stuck. Ask a friend lets them hand the question,
with an AI summary of the conversation so far, to a colleague in two clicks — by Slack or
email. Handbook delivers the message; it does not track the reply.

## Goals / non-goals / anti-goals

- **Goal:** from a "No answer found" reply in web chat, send a colleague a clear summary of the
  conversation plus the question, by Slack DM or email, and make replying to the asker one tap.
- **Non-goals:** tracking replies, saving answers as docs, asking from Slack, admin settings,
  a draft preview (the user chose to send directly).
- **Anti-goals:** leaking private documents to someone who cannot open them without the asker's
  explicit tick; a way to spam colleagues; a recipient the model chose.

## Approaches

| | Slack delivery | Verdict |
|---|---|---|
| **A (picked)** | DM from the Handbook bot, mentioning the asker; email via Handbook's email service with Reply-To = asker | Works with what is connected today |
| B | Post from the asker's own Slack account (Agent mode user token) | Needs Agent mode and a per-person sign-in: too much friction for a notification |

Showing an editable draft was considered and dropped by the user: send directly.

## Design

1. **Button.** "Ask a friend" on a web-chat reply that recorded a gap (the condition at
   `app/api/chat.py:1659`: not grounded, not access-restricted, not a chart refusal). The `done`
   event carries `ask_friend: true` when it applies.
2. **Panel.** Recipient dropdown: members of the chat's space (company chat: the company's
   users), never the asker. Channel: Slack (only if the scope has a live Slack connection and the
   recipient's email is found in that Slack team) or Email. Private-content checkbox, shown only
   when needed: "The summary includes information from private documents you can access that
   <name> can't open. I approve sending it."
3. **Private check** (`POST /ask-friend/check` → `{needs_approval, slack_available}`), recomputed
   by the send endpoint. Approval is needed when the conversation used any of: a cited document
   the recipient's `Viewer` cannot open (the one visibility predicate); an uploaded attachment;
   a private GitHub live read. Ceiling: it checks *cited* documents, not every passage the model
   saw.
4. **Summary.** The asker's chosen model (interactive, charged as any question) reads the
   conversation's turns (bounded, latest first; says when older turns were left out) and writes
   a clear, medium-length summary scaled to the conversation, then the question. Prompt carries
   `UNTRUSTED_POLICY`/`UNTRUSTED_REMINDER` around the conversation; `[n]` markers stripped; links
   kept only if they appeared verbatim in the conversation (`security/links.py`). `max_tokens`
   sized to the whole reply.
5. **Delivery.**
   - Slack: bot DM (`users.lookupByEmail`, `conversations.open`, `chat.postMessage`) —
     "<Asker> (<@asker>) asked Handbook something it couldn't answer and thought you might know.
     … Question: … Reply to <@asker> directly." (The asker's mention only when found in Slack.)
   - Email: same text, Reply-To = asker's email, via `app/auth/email.py`'s dispatcher.
6. **After.** The turn's `meta` records `ask_friend: {to_name, via, at}`; the chat shows
   "Sent to Priya via Slack ✓", also when reopened. No new table.
7. **Limits.** `limit_question` for the AI call (person + company); `limit_email` for email;
   a per-person daily cap (default 20) as its own bucket. Constants in an `AskFriendSettings`
   dataclass (`app/config/`).
8. **CLAUDE.md.** One rule: Ask a friend sends the asker's own message to a person they picked
   from their space; private content needs their tick (checked server-side); not an Act write.

## Constraints honoured

`org_id` and the asker from the session; recipient must be a member of the chat's scope
(403 otherwise); the turn must be the asker's own; one visibility predicate; untrusted policy;
link provenance; masking via the LLM factory; rate limits; heavy imports inside functions;
failures as `ProviderError`; no Act imports.

## Left to the builder

Styling and copy within the sketch; exact turn bound and token cap; module layout.
