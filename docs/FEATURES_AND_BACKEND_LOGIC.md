# Handbook: Implemented Features & Backend Logic

This file tracks newly implemented features, their configuration settings, and how the backend logic works in concise bullet points.

---

## Feature 1: In-Chat File Attachments

### What Was Done
* Added a paperclip upload button and active file chip bar to the chat interface.
* Created the `conversation_attachments` table in PostgreSQL to store extracted text.
* Implemented in-memory text extraction for PDF, Word (DOCX), CSV, and plain text.
* Configured the chat route to override standard tool routing when attachments are present.
* Added a two-tier reading system: direct prompt inclusion for short files, interactive tool paging for long files.
* Added automated 30-day background expiration for abandoned attachments.

### Configuration Settings
* **Max File Size:** 10 MB (`ATTACHMENT_MAX_BYTES`).
* **Max Attachments:** 5 files per chat session (`ATTACHMENT_MAX_PER_CONVERSATION`).
* **Short File Threshold:** 12,000 characters (~3,000 tokens) (`ATTACHMENT_INLINE_CHARS`).
* **Max Stored Text:** 400,000 characters per file (`ATTACHMENT_MAX_CHARS`).
* **Expiration TTL:** 30 days before background worker purges text (`DEFAULT_ATTACHMENT_TTL_DAYS`).

### Backend Logic & Steps
1. **Upload & Ownership Validation:**
   * Checks that the conversation belongs to the user, their organization, and their workspace.
   * Verifies file size (under 10 MB) and file count (under 5), and enforces rate limits.
2. **In-Memory Text Extraction:**
   * Parses text and tables in memory using `pypdf`, `python-docx`, and CSV sniffers.
   * Discards the raw binary file bytes immediately (`del data`). No files are saved to disk or uploaded to external CDNs.
3. **Database Storage:**
   * Saves only clean text into `conversation_attachments` with `(conversation_id, org_id, user_id)`.
   * Does not create vector embeddings or pollute the global company search index.
4. **Routing Override:**
   * Chat checks for active attachments before running routing probes.
   * If attachments exist, vector retrieval and connected tools (Notion, Drive, Slack, GitHub) are completely bypassed.
5. **Answer Cache Bypass:**
   * Completely skips reading from and writing to `query_answer_cache`, ensuring private file answers never leak to coworkers asking similar questions.
6. **AI Context Assembly:**
   * **Files <= 12,000 chars:** Full text is injected directly into the AI prompt.
   * **Files > 12,000 chars:** AI receives a 500-character preview and calls the `read_file` tool to inspect only the relevant character offsets.
7. **Cleanup & Expiration:**
   * Deleting the chat automatically deletes the attachments via `ON DELETE CASCADE`.
   * Detaching a file via the UI removes the database row immediately.
   * The background worker tick automatically purges any attachment text older than 30 days.

---

## Feature 2: User Feedback & Knowledge Gap Tracking

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

## Next Features to Implement
* Feature 3: Action Tools (Linear & GitHub Issue Creation)
* Feature 4: Jira & Confluence Connectors
* Feature 5: Deep Research Multi-Agent Planner
