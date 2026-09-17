"use client";

import { useState } from "react";
import { ApiError, FEEDBACK_REASONS, FeedbackReason, api } from "@/lib/api";
import { ChatDonePayload } from "@/lib/sse";

/**
 * Thumbs on one answer, plus the three-way reason picker a downvote opens.
 *
 * A downvote asks WHAT went wrong because the three answers lead to three
 * different fixes — re-sync a source, rewrite a document, shorten the prompt —
 * and a bare thumbs-down tells an admin only that somebody was unhappy. The
 * comment box is optional on purpose: a required one turns every downvote into
 * a form, and the vote is the part that has to be cheap.
 *
 * An ungrounded answer is ALREADY recorded as a documentation gap by the
 * backend, without anyone clicking anything, so nothing here is the only way a
 * problem gets reported — this is the extra signal for answers that came back
 * confident and wrong, which no gate can catch.
 */
export function AnswerFeedback({
  conversationId,
  question,
  done,
  workspaceId,
}: {
  conversationId: string;
  question: string;
  done: ChatDonePayload;
  workspaceId?: string | null;
}) {
  const [rating, setRating] = useState<1 | -1 | null>(null);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState<FeedbackReason | null>(null);
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(value: 1 | -1, withReason?: FeedbackReason | null, note?: string) {
    setError(null);
    try {
      await api.submitFeedback({
        conversationId,
        question,
        rating: value,
        answer: done.answer,
        resolvedQuestion: done.resolved_question,
        agent: done.agent ?? null,
        reason: withReason ?? null,
        comment: note?.trim() || null,
        workspaceId,
      });
      setRating(value);
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't send that.");
      return false;
    }
  }

  async function handleUp() {
    if (await send(1)) {
      setAsking(false);
      setSent(true);
    }
  }

  async function handleDown() {
    // Recorded on the CLICK, before the reason is picked. Someone who closes
    // the panel without choosing still meant "this was wrong", and losing that
    // because they did not fill in a form would drop the majority of votes.
    if (await send(-1)) setAsking(true);
  }

  async function handleReason(value: FeedbackReason) {
    setReason(value);
    if (await send(-1, value, comment)) {
      setAsking(false);
      setSent(true);
    }
  }

  async function handleComment(e: React.FormEvent) {
    e.preventDefault();
    if (await send(-1, reason, comment)) {
      setAsking(false);
      setSent(true);
    }
  }

  return (
    <div className="answer-feedback">
      <div className="answer-feedback-row">
        <span className="answer-feedback-label">Was this helpful?</span>
        <button
          type="button"
          className="answer-feedback-thumb"
          aria-label="This answer was helpful"
          aria-pressed={rating === 1}
          data-active={rating === 1 || undefined}
          onClick={handleUp}
        >
          <ThumbIcon />
        </button>
        <button
          type="button"
          className="answer-feedback-thumb answer-feedback-thumb-down"
          aria-label="This answer was not helpful"
          aria-pressed={rating === -1}
          data-active={rating === -1 || undefined}
          onClick={handleDown}
        >
          <ThumbIcon />
        </button>
        {sent && <span className="answer-feedback-thanks">Thanks — noted.</span>}
      </div>

      {asking && (
        <div className="answer-feedback-panel">
          <p className="answer-feedback-question">What went wrong?</p>
          <div className="answer-feedback-reasons">
            {FEEDBACK_REASONS.map((r) => (
              <button
                key={r.value}
                type="button"
                className="answer-feedback-reason"
                data-active={reason === r.value || undefined}
                onClick={() => handleReason(r.value)}
              >
                {r.label}
              </button>
            ))}
          </div>
          <form onSubmit={handleComment} className="answer-feedback-comment">
            <label className="sr-only" htmlFor="feedback-comment">
              Anything else? (optional)
            </label>
            <input
              id="feedback-comment"
              className="input"
              placeholder="Anything else? (optional)"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              maxLength={1000}
            />
            <button type="submit" className="button button-secondary button-sm">
              Send
            </button>
          </form>
        </div>
      )}

      {error && <p className="answer-feedback-error">{error}</p>}
    </div>
  );
}

/** One glyph, flipped by CSS for the down button — two nearly identical paths
 *  would be two things to keep in agreement for no gain. */
function ThumbIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden focusable="false">
      <path
        d="M6.2 14V6.6L9.1 1.6a1.2 1.2 0 0 1 2.2.8l-.7 3.1h3.1a1.2 1.2 0 0 1 1.2 1.5l-1.2 5A1.6 1.6 0 0 1 12.1 13H6.2ZM1 14h3V6.6H1Z"
        fill="currentColor"
      />
    </svg>
  );
}
