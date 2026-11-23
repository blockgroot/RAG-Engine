"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type AskFriendPerson, type AskFriendSent } from "@/lib/api";

/** A server refusal in plain words; a rate-limit message is already plain. */
function plain(e: unknown): string {
  if (!(e instanceof ApiError)) return "Something went wrong. Try again.";
  switch (e.message) {
    case "not_found": return "This answer can't be sent any more. Reload the chat.";
    case "not_in_scope": return "That person isn't in this space.";
    case "not_a_member": return "You're not a member of this space.";
    default: return e.status === 429 ? e.message : "Something went wrong. Try again.";
  }
}

/**
 * "No answer found" → send one colleague an AI summary of this chat plus the
 * question, by Slack or email. One colleague per question: once sent, the
 * button becomes the "Sent to" line. A failed send can be retried or moved to
 * email; the server records nothing until delivery worked.
 */
export function AskFriend({
  conversationId,
  question,
  workspaceId,
  model,
  turnIndex,
  sent: storedSent,
}: {
  conversationId: string;
  question: string;
  workspaceId?: string | null;
  model?: string | null;
  turnIndex?: number;
  sent?: AskFriendSent | null;
}) {
  const [sent, setSent] = useState<AskFriendSent | null>(storedSent ?? null);
  const [open, setOpen] = useState(false);
  const [people, setPeople] = useState<AskFriendPerson[] | null>(null);
  const [recipient, setRecipient] = useState("");
  const [via, setVia] = useState<"slack" | "email">("email");
  const [slackAvailable, setSlackAvailable] = useState(false);
  const [needsApproval, setNeedsApproval] = useState(false);
  const [approved, setApproved] = useState(false);
  const [checking, setChecking] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [failedVia, setFailedVia] = useState<"slack" | "email" | null>(null);

  useEffect(() => setSent(storedSent ?? null), [storedSent]);

  useEffect(() => {
    if (!open || people) return;
    api.askFriendPeople(workspaceId)
      .then((r) => setPeople(r.people))
      .catch(() => setError("Couldn't load people. Try again."));
  }, [open, people, workspaceId]);

  useEffect(() => {
    if (!recipient) return;
    let live = true;
    setChecking(true);
    setApproved(false);
    setNeedsApproval(false);
    setSlackAvailable(false);
    setFailedVia(null);
    setError(null);
    api.askFriendCheck({ conversationId, question, recipientUserId: recipient, workspaceId, turnIndex })
      .then((r) => {
        if (!live) return;
        setNeedsApproval(r.needs_approval);
        setSlackAvailable(r.slack_available);
        setVia(r.slack_available ? "slack" : "email");
      })
      .catch((e) => live && setError(plain(e)))
      .finally(() => live && setChecking(false));
    return () => {
      live = false;
    };
  }, [recipient, conversationId, question, workspaceId, turnIndex]);

  if (sent) {
    return (
      <p className="ask-friend-sent">
        Sent to {sent.to_name} via {sent.via === "slack" ? "Slack" : "email"} ✓
      </p>
    );
  }

  const person = people?.find((p) => p.user_id === recipient);

  async function send(channel: "slack" | "email") {
    setSending(true);
    setError(null);
    setFailedVia(null);
    try {
      const r = await api.askFriendSend({
        conversationId, question, recipientUserId: recipient, workspaceId, turnIndex,
        via: channel, approvePrivate: approved, model,
      });
      setSent(r.sent);
    } catch (e) {
      if (e instanceof ApiError && e.status === 502) {
        setFailedVia(channel);
      } else if (e instanceof ApiError && e.message === "already_sent") {
        setError("This question was already sent to a colleague.");
        setOpen(false);
      } else if (e instanceof ApiError && e.message === "approval_required") {
        setNeedsApproval(true);
        setError("Please confirm sharing private information first.");
      } else if (e instanceof ApiError && e.message === "slack_unavailable") {
        setError(`${person?.name ?? "They"} can't be reached on Slack. Send by email instead.`);
        setSlackAvailable(false);
        setVia("email");
      } else {
        setError(plain(e));
      }
    } finally {
      setSending(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="chat-chart-hint" onClick={() => setOpen(true)}>
        Ask a friend
      </button>
    );
  }

  const ready = !!recipient && !checking && !sending && (!needsApproval || approved);

  return (
    <div className="ask-friend-panel">
      <label className="ask-friend-row">
        <span>Send to</span>
        <select value={recipient} onChange={(e) => setRecipient(e.target.value)} disabled={!people || sending}>
          <option value="">{people ? (people.length ? "Pick a colleague" : "No one else here") : "Loading…"}</option>
          {people?.map((p) => (
            <option key={p.user_id} value={p.user_id}>
              {p.name === p.email ? p.email : `${p.name} (${p.email})`}
            </option>
          ))}
        </select>
      </label>
      {recipient && (
        <fieldset className="ask-friend-row" disabled={sending}>
          <span>Via</span>
          <label>
            <input type="radio" checked={via === "slack"} disabled={!slackAvailable}
                   onChange={() => setVia("slack")} /> Slack
          </label>
          <label>
            <input type="radio" checked={via === "email"} onChange={() => setVia("email")} /> Email
          </label>
          {!checking && !slackAvailable && <span className="ask-friend-note">Not in your Slack</span>}
        </fieldset>
      )}
      {recipient && needsApproval && (
        <label className="ask-friend-approve">
          <input type="checkbox" checked={approved} onChange={(e) => setApproved(e.target.checked)} />
          The summary includes information from private documents you can access
          that {person?.name ?? "they"} can&apos;t open. I approve sending it.
        </label>
      )}
      {failedVia ? (
        <div className="ask-friend-failed" role="alert">
          <span>Failed to send.</span>
          <button type="button" className="chat-chart-hint" disabled={sending} onClick={() => send(failedVia)}>
            Send again
          </button>
          {failedVia === "slack" && (
            <button type="button" className="chat-chart-hint" disabled={sending}
                    onClick={() => { setVia("email"); void send("email"); }}>
              Send by email instead
            </button>
          )}
        </div>
      ) : (
        <div className="ask-friend-actions">
          <button type="button" className="chat-chart-hint" onClick={() => setOpen(false)} disabled={sending}>
            Cancel
          </button>
          <button type="button" className="chat-chart-hint ask-friend-send" disabled={!ready}
                  onClick={() => send(via)}>
            {sending ? "Sending…" : "Send"}
          </button>
        </div>
      )}
      {error && <p className="ask-friend-error" role="alert">{error}</p>}
    </div>
  );
}
