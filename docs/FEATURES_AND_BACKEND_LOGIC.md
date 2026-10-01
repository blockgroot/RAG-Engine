# Handbook: Implemented Features & Backend Logic

*Last updated: 1 October 2026.* This file tracks newly implemented features, their configuration settings, and how the backend logic works in concise bullet points.

---

## Feature 1: In-Chat File Attachments — Live

### What Was Done
* Paperclip upload button and a file chip bar in chat; several files per upload, each accepted
  or refused on its own with a reason.
* Files (PDF, DOCX, CSV, TSV, TXT, MD, LOG, JSON) are extracted once at upload.
* **The original bytes and the extracted text are stored in Cloudinary** (authenticated, raw
  assets); `conversation_attachments` keeps metadata and a `storage_key` only.
* **An attachment joins retrieval, it does not replace it**: routing and the routed agent run
  as normal and the file's text is added to the context, so "is this bill claimable?" is
  checked against the bill and the expense policy together. On a relevance-gate miss the
  answer comes from the files alone.
* Short files go into the prompt whole; long files are paged with a `read_file` tool.
* Uploads are screened by the prompt-injection guard (up to ~15.6K characters) and accepted
  with a warning chip when flagged.

### Configuration Settings
* **Max File Size:** 10 MB (`ATTACHMENT_MAX_BYTES`).
* **Max Attachments:** 5 per conversation (`ATTACHMENT_MAX_PER_CONVERSATION`).
* **Max Tokens per file:** 120,000, checked at upload; CSV/TSV exempt (`ATTACHMENT_MAX_TOKENS`).
* **Short File Threshold:** 12,000 characters (`ATTACHMENT_INLINE_CHARS`).
* **Max Stored Text:** 400,000 characters per file (`ATTACHMENT_MAX_CHARS`).
* **Expiration:** 30 days; unused uploads after 24 hours (`app/attachments/store.py`).
* **Storage:** `CLOUDINARY_*` credentials, both-or-neither; use a separate `CLOUDINARY_FOLDER`
  per environment.

### Backend Logic & Steps
1. **Upload & ownership validation:** the conversation must belong to the user, their org and
   their space; size, count, token and rate limits are enforced per file.
2. **Extraction:** `pypdf`, `python-docx` and CSV sniffing, in memory.
3. **Storage:** a row is created first, then the original and a `plaintext_<id>` companion are
   uploaded to Cloudinary; a storage failure deletes the row and any half-written assets.
4. **Answering:** routing runs first; the files join the routed agent's context
   (`rag/pipeline.py::_run`, `extra_contexts`). Every block names itself, so blending is
   traceable.
5. **Cache:** an answer that used an attachment is never cached.
6. **Cleanup:** deleting the chat or detaching a file deletes the row first, then the assets
   (best effort); the tick sweeps expired attachments and their assets.

---

## Feature 2: User Feedback & Knowledge Gap Tracking — Live

### What Was Done
* Created the `feedback_and_gaps` table in PostgreSQL to store ungrounded refusals and user ratings in a single schema.
* Added automatic refusal logging at the API edge (`app/api/chat.py` and `app/api/slack_events.py`) whenever `not result.grounded`.
* Added the `POST /chat/feedback` endpoint for users to submit thumbs up/down ratings, closed reason categories, and comments.
* Implemented idempotent merging: downvoting an already-refused question updates the existing row rather than duplicating records.
* Added the `GET /admin/feedback` endpoint for admins to inspect gaps grouped by normalized questions and ranked by distinct employees.

### Configuration Settings & Limits
* **Max Comment Length:** 1,000 characters (`MAX_COMMENT_CHARS`).
* **Admin Window Range:** Default 30 days, clamped to a maximum of 365 days (`MAX_WINDOW_DAYS`).
* **Max Admin Result Rows:** 50 rows per list (`MAX_ROWS`).
* **Downvote Categories:** Closed set: `incorrect`, `out_of_date`, `unhelpful` (`RATING_REASONS`).
* **User Retention:** `user_id ON DELETE SET NULL` so organizational gaps survive employee offboarding.

### Backend Logic & Steps
1. **Automatic Refusal Detection at the Edge:**
   * Checks `if not result.grounded` right before streaming tokens in Web Chat and Slack.
   * Catches all 4 refusal paths: gate misses (< 0.35), prompt refusals, audit citation downgrades, and GitHub/Insights tool failures.
   * Normalizes the resolved standalone question (stripping punctuation and whitespace) and records `gate_score` as a diagnostic.
   * Swallows database errors so logging never interrupts user answers.
2. **User Rating Submission:**
   * Validates conversation ownership (`_conversation_belongs_to_scope`), enforces rate limiting, and validates ratings (-1 or +1).
   * Restricts downvote reasons to `incorrect`, `out_of_date`, or `unhelpful`.
3. **Idempotent Database Merging:**
   * If an ungrounded refusal row already exists for that conversation and normalized question, it updates that row with the rating, reason, and comment.
   * If no row exists (rating an answer that succeeded), it inserts a new record.
   * Re-clicking a thumb updates the existing record instead of creating duplicate votes.
4. **Admin Gap Reporting:**
   * Groups records by `normalized_question` and ranks them by `COUNT(DISTINCT user_id)` to prioritize topics affecting multiple people.
   * Reports `best_gate_score` to distinguish between completely missing documents (null score) and retrieval-reachability issues (score near 0.35).
   * Returns summary counts, top gaps, and recent downvoted feedback with comments in a single round-trip.

---

## Other implemented features

This file covers two features in depth. The full list of what is live, verified, blocked and in
progress is in `PRODUCT_STATUS.md`.

## Next Features to Implement
* Action tools (Linear and GitHub issue creation) with explicit confirmation
* Jira and Confluence connectors
* Deep research multi-agent planner
* Open-ended charts (PR #45, built, in review)
