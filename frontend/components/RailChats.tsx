"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, usePathname, useRouter } from "next/navigation";
import { api, ConversationSummary } from "@/lib/api";

function ChatIco() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function formatRelativeTime(dateStr: string): string {
  try {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / (1000 * 60));
    if (diffMins < 1) return "now";
    if (diffMins < 60) return `${diffMins}m`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays === 1) return "1d";
    if (diffDays < 7) return `${diffDays}d`;
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

/**
 * Minimalist recent chats list in the left rail under Explore.
 * Displays only the user's top-5 conversations with 1-click access.
 */
export function RailChats() {
  const params = useParams<{ id?: string }>();
  const pathname = usePathname();
  const router = useRouter();

  const workspaceId = params?.id ?? null;
  const targetChatPath = workspaceId ? `/workspaces/${workspaceId}/ask` : "/chat";
  const isCurrentChatPath =
    pathname === "/chat" ||
    (Boolean(workspaceId) && pathname === `/workspaces/${workspaceId}/ask`);

  const [items, setItems] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(() => {
    api
      .listConversations(workspaceId)
      .then(({ conversations }) => setItems(conversations))
      .catch(() => undefined)
      .finally(() => setLoaded(true));
  }, [workspaceId]);

  useEffect(() => {
    load();
  }, [load]);

  // Sync active conversation and updates via window events
  useEffect(() => {
    function onActiveChanged(e: Event) {
      const custom = e as CustomEvent<{ id: string | null }>;
      setActiveId(custom.detail?.id ?? null);
    }
    function onChatsUpdated() {
      load();
    }
    window.addEventListener("active-conversation-changed", onActiveChanged);
    window.addEventListener("chats-updated", onChatsUpdated);
    return () => {
      window.removeEventListener("active-conversation-changed", onActiveChanged);
      window.removeEventListener("chats-updated", onChatsUpdated);
    };
  }, [load]);

  // Read active conversation from sessionStorage on mount
  useEffect(() => {
    try {
      const key = `chat.conversation.${workspaceId ?? "org"}`;
      const saved = sessionStorage.getItem(key);
      if (saved) setActiveId(saved);
    } catch {
      /* storage blocked */
    }
  }, [workspaceId]);

  function handleOpen(id: string) {
    if (isCurrentChatPath) {
      window.dispatchEvent(new CustomEvent("open-conversation", { detail: { id } }));
    } else {
      router.push(`${targetChatPath}?c=${encodeURIComponent(id)}`);
    }
  }

  const top5 = items.slice(0, 5);

  if (!loaded || top5.length === 0) {
    return null;
  }

  return (
    <div className="rail-chats" aria-label="Recent chats">
      <div className="rail-chats-head">
        <span className="rail-chats-title">Recent Chats</span>
        <span className="rail-chats-badge">{top5.length}</span>
      </div>

      <ul className="rail-chats-list" role="list">
        {top5.map((c) => {
          const isActive = isCurrentChatPath && c.id === activeId;
          const timeLabel = formatRelativeTime(c.last_activity_at || c.created_at);
          const title = c.title || "Untitled chat";

          return (
            <li key={c.id}>
              <button
                type="button"
                className={`rail-chat-item${isActive ? " is-active" : ""}`}
                onClick={() => handleOpen(c.id)}
                title={title}
              >
                <span className="rail-chat-ico" aria-hidden>
                  <ChatIco />
                </span>
                <span className="rail-chat-label">{title}</span>
                {timeLabel && <span className="rail-chat-time">{timeLabel}</span>}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
