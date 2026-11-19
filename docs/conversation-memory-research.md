# Conversation memory: how production AI chat systems do it, and what it means for us

Research only. Nothing here is implemented. Date: 19 Nov 2026.

## 1. The question

We store every chat turn (question + answer) in Postgres, plus a running summary
per conversation. Is that the wrong approach for many employees × many chats?
How do ChatGPT and Claude keep a conversation's context, and how is it used when
the next question arrives in the same chat?

## 2. Short answer

- **Storing the full transcript server-side is what every production chat product
  does.** ChatGPT, Claude.ai and OpenAI's own Conversations API all persist every
  message in a database, so a chat can be reopened, searched, exported and
  continued from any device. Storage is not the problem to solve; text is tiny.
- **What differs between systems is not *whether* history is stored but *what is
  sent to the model* on each new turn.** That is called the *context window
  strategy*: full history up to a token budget, then truncation or compaction
  (summarise the oldest part).
- **Our real gap is on the use side, not the storage side.** Today only the
  question-rewriter sees the history; the model that writes the answer sees none
  of it (see §4). The rest of our storage design (one summary per chat, a fold
  marker, 30-day retention, cascade deletes, per-org scoping) is already in line
  with standard practice.

## 3. How others do it

### 3.1 ChatGPT (consumer product)

Not officially documented; the following is from the data-export format and
reverse-engineering write-ups, so treat field names as unverified.

- **Storage:** each conversation is a *tree* of messages, not a flat list. Every
  message node has an id, a `parent`, `children`, and the conversation points at
  the active leaf (`current_node`). Editing a question or regenerating an answer
  adds a sibling branch under the same parent; the visible chat is the path from
  root to the active leaf. Everything is kept server-side.
- **Per-turn context:** the active branch (root → leaf) is sent to the model,
  trimmed to the model's context budget when the chat gets long.
- **Cross-chat memory**, which is a separate system from the chat itself:
  - *Saved memories*: short facts about the user, visible and deletable, injected
    into the system prompt on every message.
  - *Chat history reference*: summaries of recent conversations / user insights
    injected as context. Whether any retrieval is involved is disputed.

### 3.2 Claude.ai (consumer product)

- **Storage:** every chat is stored server-side (incognito chats excepted).
- **Per-turn context:** the conversation is sent to the model; when it nears the
  context limit, older context is compacted into a summary.
- **Cross-chat memory**, also separate from the chat:
  - *Memory*: summarised facts about the user and their projects, not a transcript.
  - *Chat search*: a tool the model calls only when needed (reported as
    `conversation_search` / `recent_chats`), which retrieves the original past
    chats and cites them. It is a tool call, not something injected every turn.

### 3.3 The developer APIs (what the vendors recommend builders do)

- **OpenAI Responses + Conversations API:** a conversation is a durable server
  object with its own id, usable across sessions and devices. Chaining with
  `previous_response_id` re-sends the whole chain, and *every prior token is
  billed again as input on each turn*. Older items can be truncated
  (`truncation: "auto"`) or compacted.
- **Anthropic Messages API:** stateless; the client stores the history and sends
  it each turn. For long chats Anthropic's recommended primary strategy is
  *server-side compaction* (`context_management` with the compaction edit): once
  the prompt passes a token threshold you set, the API summarises the older part
  and returns a compaction block that you store and send back instead of the old
  messages. *Context editing* (dropping old tool results) is the other lever.

### 3.4 Conversational RAG specifically

For retrieval systems the standard pattern (LangChain's condense-question chain,
LlamaIndex's condense engine, the published research) is:

1. **Rewrite** the follow-up into a standalone query using the recent turns, then
   retrieve with that. A pronoun-laden follow-up retrieves the wrong chunks
   otherwise. Rewriters usually see a short window (6–10 messages).
2. **Generate** the answer from the retrieved passages. Whether the answer prompt
   also gets the recent turns varies by product; most chat-style RAG products
   include a short window so the reply reads as a continuation ("as above", "the
   second one", no repeating what was just said).

## 4. Where we stand today

| Concern | Industry practice | Us |
|---|---|---|
| Transcript stored server-side | Yes, all of them | Yes: `conversation_turns`, one row per turn, `cited`/`chart`/`meta` JSON |
| Scoped per user / tenant | Per account | `org_id` + `user_id` + `workspace_id`; owner predicate on delete/rename |
| Long-chat handling | Token-threshold compaction | Fixed window of 3 turns verbatim + one running summary, folded in background (`folded_through` marker) |
| History used for retrieval | Rewrite the query | Yes: rewriter sees summary + last 3 turns |
| History used for the answer | Usually a short window | **No.** The answer model sees only the rewritten question + passages |
| Branching (edit / regenerate) | Tree with `parent_id` | Linear list, `turn_index` |
| Retention | User-controlled, policy-based | Purged 30 days after last activity (`purge_expired_conversations`, worker) |
| Cross-chat memory | Saved facts + chat search | Personal memory (facts from the asker's own questions, off by default); no chat search |
| Deletion | User can delete | Yes; turns, attachments, last-retrieval cascade |

### Is the storage itself a cost problem?

Rough sizing: 1,000 employees × 20 questions/day × ~4 KB per turn (question,
answer, cited JSON) ≈ 80 MB/day. With the 30-day purge, the steady state is about
2.4 GB, a small table for Postgres. The only index needed for the hot path,
`(conversation_id)`, exists. **No change is needed for scale.** If it ever
matters, the levers are, in order: a shorter retention, then partitioning
`conversation_turns` by month (drop a partition instead of a DELETE).

## 5. Gaps worth deciding on

Ordered by value. Each is a decision, not a task.

### G1. The answer model has no conversation context (highest value)

A follow-up gets the right *passages* through the rewrite, but the answer is
written as if it were the first question: it may repeat the previous answer,
cannot honour "shorter", "in a table", or "only the second one", and cannot
answer "what did you just say?".

Option: give the answer prompt the last N turns (or the summary plus the last
turn) as **conversation context, explicitly not evidence**: fenced with
`UNTRUSTED_POLICY` / `UNTRUSTED_REMINDER`, never citable, and behind the
unchanged 0.35 gate and grounded prompt. Things to resolve first:

- **Grounding:** a previous answer must not become a source for a new fact. The
  prompt must say facts come only from PASSAGES. This needs an eval on
  follow-ups, including one where the earlier answer was wrong.
- **Access:** an earlier answer may quote a document the asker can no longer
  open (sharing revoked since). Context from old turns must not re-expose it.
  Options: send only the asker's own questions plus answer *shape*, or re-check
  the cited documents for the current viewer before including an answer.
- **Prompt order:** `MODE:` must stay first and CONTEXT / QUESTION must not move
  earlier (CLAUDE.md §5 Model). The history block goes before CONTEXT.
- **Cost:** about +1–2k input tokens per follow-up; tie into `ai_spend`.

### G2. A fixed 3-turn window instead of a token budget

The vendors size the window by tokens, not turns: one long chart or table answer
can be larger than ten short ones. Option: keep the newest turns while they fit a
token budget (e.g. 3–4k tokens), fold the rest. `MEMORY_RECENT_TURNS` stays as
an upper bound. Low risk; local to `get_context` and `_update_running_summary`.

### G3. Native compaction instead of our own fold

Since the app is Claude-only now, Anthropic's server-side compaction could replace
`build_summary_prompt` + the background fold. **Recommendation: do not switch.**
Our fold runs on the cheap background model, off the answer path, is fenced
against injection, and is already token-flat. Native compaction would run on the
answering model, couple us to a beta API, and it only pays off for agent-style
chats that send long histories every turn, which we do not do. Revisit only if
G1 makes us send long histories to the answer model.

### G4. Questions about the chat itself ("summarise our conversation")

ChatGPT and Claude answer these from the history they send. We route them to
document search, which refuses. Options: (a) G1 covers the simple cases; (b) a
small classifier route ("about this chat") that answers from the transcript
alone, labelled as such, with no document claims. Decide after G1.

### G5. Branching (edit a question / regenerate an answer)

Needs a `parent_turn_id` and an active-leaf pointer, as ChatGPT does. **Only if
the product wants edit/regenerate in the UI**; otherwise YAGNI.

### G6. Search across past chats

Claude.ai's model is the one to copy if we want it: a tool the model calls when
needed, scoped to the asker's own chats, results cited. Not needed for a
company-knowledge product unless users ask for it.

### Not recommended

- **A cache of answers keyed on the question or scope.** CLAUDE.md §3 already
  rules this out: it would serve one person's answer to the next asker.
- **Storing only summaries and dropping turns.** Users expect to reopen a chat
  exactly as it was, and citations/charts are stored on the turn for that reason.
- **Vector-indexing all chats for retrieval.** Every source above treats chat
  memory as either injected summaries or an on-demand tool, never as part of
  the document index. Mixing them would let old answers compete with documents
  as evidence.

## 6. Suggested next step

Run an eval of G1 on 20–30 real follow-up chains (pronoun follow-ups, "shorter",
"as a table", "what about X instead", one with a wrong earlier answer, one with
revoked access) comparing today vs. the answer model given the last 2 turns.
Decide G1 from that. G2 is safe to take alongside it.

## Sources

- ChatGPT export format, message tree: <https://ai-chat-importer.com/guides/chatgpt-export-format-explained>
- Branching conversations stored as a parent-pointer tree: <https://trigger.dev/docs/ai-chat/patterns/branching-conversations.md>
- ChatGPT-style conversation system design: <https://liviaerxin.github.io/docs/system-design/building-chatgpt-style-conversation>
- ChatGPT memory, reverse engineered (unofficial): <https://llmrefs.com/blog/reverse-engineering-chatgpt-memory>, <https://www.gigazine.net/gsc_news/en/20251226-reverse-engineered-chatgpt-memory-system>
- Claude chat search and memory (official): <https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context.md>
- Claude memory internals (unofficial): <https://scour.ing/@emschwartz/p/https://manthanguptaa.in/posts/claude_memory>
- Anthropic compaction: <https://platform.claude.com/docs/en/build-with-claude/compaction>, context editing: <https://platform.claude.com/docs/en/build-with-claude/context-editing.md>
- OpenAI conversation state (Conversations API, `previous_response_id`, billing): <https://developers.openai.com/api/docs/guides/conversation-state>
- Query rewriting in conversational RAG: <https://shekhargulati.com/2024/07/17/query-rewriting-in-rag-applications/>, CHIQ (EMNLP 2024): <https://preview.aclanthology.org/setup/2024.emnlp-main.135>, multi-query rewriting: <https://arxiv.org/pdf/2406.18960>
