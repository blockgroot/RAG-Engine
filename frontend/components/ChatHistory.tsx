"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ConversationSummary } from "@/lib/api";

/** The list of this person's chats in this scope.
 *
 * Why it exists: `conversationId` used to live in a React ref, so a refresh
 * started a new chat and the old one became unreachable -- with its uploaded
 * files still attached to it and no way to remove them. The list is what gives
 * that control back, and it is also the only sensible home for "delete this
 * chat", which had no home at all.
 *
 * Every surviving chat is shown. There is deliberately NO display cap: a cap
 * plus a longer retention leaves a live chat off the end of the list,
 * unreachable and undeletable, which is the bug this replaces. Retention is
 * the list length, and the panel says so out loud -- silent deletion of
 * someone's work is fine when disclosed and a complaint when discovered.
 */
export function ChatHistory({
  workspaceId,
  activeId,
  onOpen,
  onNew,
  reloadKey,
}: {
  workspaceId: string | null;
  activeId: string | null;
  onOpen: (id: string) => void;
  onNew: () => void;
  /** Bumped by the parent after a turn, so a new chat appears without a reload. */
  reloadKey: number;
}) {
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [retentionDays, setRetentionDays] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(() => {
    api
      .listConversations(workspaceId)
      .then(({ conversations, retention_days }) => {
        setItems(conversations);
        setRetentionDays(retention_days);
      })
      // History is an enhancement: asking still works without it, so a failure
      // here must never render as a chat error.
      .catch(() => undefined)
      .finally(() => setLoaded(true));
  }, [workspaceId]);

  useEffect(load, [load, reloadKey]);

  async function remove(id: string, event: React.MouseEvent) {
    event.stopPropagation();
    // Confirmed because it also destroys the attachments and the transcript,
    // and there is no undo -- the cascade is the point of the button, not a
    // side effect to discover afterwards.
    if (!window.confirm("Delete this chat, its messages and any files attached to it?")) {
      return;
    }
    setItems((prev) => prev.filter((c) => c.id !== id));
    try {
      await api.deleteConversation(id, workspaceId);
    } catch {
      load(); // put it back if the server disagreed
    }
    if (id === activeId) onNew();
  }

  if (loaded && items.length === 0) return null;

  return (
    <aside className="chat-history" aria-label="Your chats">
      <div className="chat-history-head">
        <span className="chat-history-title">Your chats</span>
        <button type="button" className="chat-history-new" onClick={onNew}>
          New
        </button>
      </div>
      <ul className="chat-history-list">
        {items.map((c) => (
          <li key={c.id}>
            <button
              type="button"
              className={`chat-history-item${c.id === activeId ? " is-active" : ""}`}
              onClick={() => onOpen(c.id)}
            >
              <span className="chat-history-label">{c.title || "Untitled chat"}</span>
              {c.attachment_count > 0 && (
                <span
                  className="chat-history-clip"
                  title={`${c.attachment_count} file${c.attachment_count === 1 ? "" : "s"} attached`}
                  aria-label="has attachments"
                >
                  ◍
                </span>
              )}
              <span
                className="chat-history-del"
                role="button"
                tabIndex={0}
                aria-label="Delete this chat"
                onClick={(e) => remove(c.id, e)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    remove(c.id, e as unknown as React.MouseEvent);
                  }
                }}
              >
                ×
              </span>
            </button>
          </li>
        ))}
      </ul>
      {retentionDays !== null && (
        <p className="chat-history-retention">Chats are kept for {retentionDays} days.</p>
      )}
    </aside>
  );
}
