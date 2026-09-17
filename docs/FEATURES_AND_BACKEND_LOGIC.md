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

## Next Features to Implement
* Feature 2: User Feedback & Knowledge Gap Tracking
* Feature 3: Action Tools (Linear & GitHub Issue Creation)
* Feature 4: Jira & Confluence Connectors
