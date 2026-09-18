"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
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

function ChatBubbleIcon() {
  return (
    <svg className="chat-history-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
      <line x1="12" y1="5" x2="12" y2="19" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      <line x1="5" y1="12" x2="19" y2="12" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
      <path d="m21 21-4.35-4.35" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function CollapseIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="3" y="3" width="18" height="18" rx="2.5" stroke="currentColor" strokeWidth="1.75" />
      <line x1="15" y1="3" x2="15" y2="21" stroke="currentColor" strokeWidth="1.75" />
      <path d="m10 10 2 2-2 2" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
      <polyline points="3 6 5 6 21 6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path
        d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PaperclipMiniIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ShieldClockIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.75" />
      <polyline points="12 7 12 12 15 14" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function formatRelativeTime(dateStr: string): string {
  try {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / (1000 * 60));
    if (diffMins < 1) return "Just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays === 1) return "Yesterday";
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

type BucketKey = "today" | "yesterday" | "last7" | "older";

const BUCKET_LABELS: Record<BucketKey, string> = {
  today: "Today",
  yesterday: "Yesterday",
  last7: "Previous 7 Days",
  older: "Older",
};

function getBucket(dateStr: string): BucketKey {
  try {
    const date = new Date(dateStr);
    const now = new Date();
    const nowDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const targetDate = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    const diffDays = Math.round((nowDate.getTime() - targetDate.getTime()) / (1000 * 60 * 60 * 24));

    if (diffDays <= 0) return "today";
    if (diffDays === 1) return "yesterday";
    if (diffDays <= 7) return "last7";
    return "older";
  } catch {
    return "older";
  }
}

export function ChatHistory({
  workspaceId,
  activeId,
  onOpen,
  onNew,
  reloadKey,
  collapsed = false,
  onToggleCollapse,
}: {
  workspaceId: string | null;
  activeId: string | null;
  onOpen: (id: string) => void;
  onNew: () => void;
  /** Bumped by the parent after a turn, so a new chat appears without a reload. */
  reloadKey: number;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}) {
  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [retentionDays, setRetentionDays] = useState<number | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const searchInputId = useId();
  const searchInputRef = useRef<HTMLInputElement>(null);

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

  // Close confirmation if user hits Escape
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setConfirmingId(null);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  async function remove(id: string) {
    setConfirmingId(null);
    setItems((prev) => prev.filter((c) => c.id !== id));
    try {
      await api.deleteConversation(id, workspaceId);
    } catch {
      load(); // put it back if the server disagreed
    }
    if (id === activeId) onNew();
  }

  function handlePromptDelete(id: string, event: React.MouseEvent) {
    event.stopPropagation();
    setConfirmingId(id);
  }

  // Filter items by title search
  const filteredItems = searchQuery.trim()
    ? items.filter((c) =>
        (c.title || "Untitled chat")
          .toLowerCase()
          .includes(searchQuery.trim().toLowerCase()),
      )
    : items;

  // Group items by date bucket
  const groups: { key: BucketKey; label: string; items: ConversationSummary[] }[] = [];
  const order: BucketKey[] = ["today", "yesterday", "last7", "older"];

  for (const key of order) {
    const matching = filteredItems.filter(
      (c) => getBucket(c.last_activity_at || c.created_at) === key,
    );
    if (matching.length > 0) {
      groups.push({ key, label: BUCKET_LABELS[key], items: matching });
    }
  }

  // Always rendered, even empty. Unmounting it moved Ask sideways the moment
  // the first chat was saved -- a layout jump on the one interaction this
  // column exists for -- and it took "New chat" away from the person with no
  // chats, who is exactly who needs it.
  return (
    <aside
      className={`chat-history${collapsed ? " is-collapsed" : ""}`}
      aria-label="Your chats"
    >
      <div className="chat-history-head">
        <div className="chat-history-title-wrap">
          <span className="chat-history-title">Your chats</span>
          {loaded && items.length > 0 && (
            <span className="chat-history-badge" title={`${items.length} conversations saved`}>
              {items.length}
            </span>
          )}
        </div>

        <div className="chat-history-actions">
          <button
            type="button"
            className="chat-history-new"
            onClick={onNew}
            title="Start a new chat"
          >
            <PlusIcon />
            <span>New</span>
          </button>
          {onToggleCollapse && (
            <button
              type="button"
              className="chat-history-collapse-btn"
              onClick={onToggleCollapse}
              title="Collapse chats sidebar"
              aria-label="Collapse chats sidebar"
            >
              <CollapseIcon />
            </button>
          )}
        </div>
      </div>

      {items.length > 2 && (
        <div className="chat-history-search">
          <label htmlFor={searchInputId} className="sr-only">
            Filter conversations
          </label>
          <div className="chat-history-search-wrap">
            <span className="chat-history-search-ico" aria-hidden>
              <SearchIcon />
            </span>
            <input
              ref={searchInputRef}
              id={searchInputId}
              type="text"
              className="chat-history-search-input"
              placeholder="Filter chats…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                type="button"
                className="chat-history-search-clear"
                onClick={() => {
                  setSearchQuery("");
                  searchInputRef.current?.focus();
                }}
                aria-label="Clear filter"
              >
                ×
              </button>
            )}
          </div>
        </div>
      )}

      {loaded && items.length === 0 && (
        <div className="chat-history-empty-card">
          <div className="chat-history-empty-glyph">
            <ChatBubbleIcon />
          </div>
          <p className="chat-history-empty-title">No conversations yet</p>
          <p className="chat-history-empty-desc">
            Questions you ask will be saved here so you can return to them anytime.
          </p>
          <button type="button" className="chat-history-empty-action" onClick={onNew}>
            Ask your first question
          </button>
        </div>
      )}

      {loaded && items.length > 0 && filteredItems.length === 0 && (
        <div className="chat-history-no-match">
          <p className="chat-history-empty-desc">No chats matching “{searchQuery}”</p>
          <button
            type="button"
            className="chat-history-clear-filter-btn"
            onClick={() => setSearchQuery("")}
          >
            Clear filter
          </button>
        </div>
      )}

      <div className="chat-history-list" role="list">
        {groups.map((g) => (
          <div key={g.key} className="chat-history-group">
            <div className="chat-history-group-label">{g.label}</div>
            <ul className="chat-history-group-items">
              {g.items.map((c) => {
                const isActive = c.id === activeId;
                const isConfirming = confirmingId === c.id;
                const timeLabel = formatRelativeTime(c.last_activity_at || c.created_at);

                return (
                  <li key={c.id} className="chat-history-row">
                    <button
                      type="button"
                      className={`chat-history-item${isActive ? " is-active" : ""}`}
                      onClick={() => onOpen(c.id)}
                    >
                      <span className="chat-history-item-ico" aria-hidden>
                        <ChatBubbleIcon />
                      </span>
                      <div className="chat-history-item-content">
                        <span className="chat-history-label" title={c.title || "Untitled chat"}>
                          {c.title || "Untitled chat"}
                        </span>
                        <div className="chat-history-meta">
                          {timeLabel && <span className="chat-history-time">{timeLabel}</span>}
                          {c.attachment_count > 0 && (
                            <span
                              className="chat-history-clip-tag"
                              title={`${c.attachment_count} file${c.attachment_count === 1 ? "" : "s"} attached`}
                            >
                              <PaperclipMiniIcon />
                              <span>{c.attachment_count}</span>
                            </span>
                          )}
                        </div>
                      </div>

                      <span
                        className="chat-history-del"
                        role="button"
                        tabIndex={0}
                        aria-label="Delete this chat"
                        title="Delete this chat"
                        onClick={(e) => handlePromptDelete(c.id, e)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            handlePromptDelete(c.id, e as unknown as React.MouseEvent);
                          }
                        }}
                      >
                        <TrashIcon />
                      </span>
                    </button>

                    {isConfirming && (
                      <div className="chat-history-popover" role="dialog" aria-label="Confirm deletion">
                        <p className="chat-history-popover-text">Delete chat and attachments?</p>
                        <div className="chat-history-popover-btns">
                          <button
                            type="button"
                            className="chat-history-popover-del"
                            onClick={(e) => {
                              e.stopPropagation();
                              void remove(c.id);
                            }}
                          >
                            Delete
                          </button>
                          <button
                            type="button"
                            className="chat-history-popover-cancel"
                            onClick={(e) => {
                              e.stopPropagation();
                              setConfirmingId(null);
                            }}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      {retentionDays !== null && (
        <div className="chat-history-retention-card">
          <span className="chat-history-retention-ico" aria-hidden>
            <ShieldClockIcon />
          </span>
          <span className="chat-history-retention-text">
            Chats kept for {retentionDays} days · Personal
          </span>
        </div>
      )}
    </aside>
  );
}
