"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, usePathname, useRouter } from "next/navigation";
import { api, ConversationSummary } from "@/lib/api";
import { chatsCacheKey, getCachedChats, setCachedChats } from "@/lib/chatsCache";

function TrashIco() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m2 0v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V6M10 11v6M14 11v6"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ChevronIco() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M6 9l6 6 6-6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

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

// One key for every scope: "do I want this section open?" is a preference
// about the RAIL, not about which space you are standing in, and keying it per
// workspace would make the section collapse and reappear as you move around.
const COLLAPSED_KEY = "rail.chats.collapsed";

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

  // Seeded from the cache so a remount renders the list it already had rather
  // than blanking and re-fetching. `loaded` starts true when there IS a cached
  // list, because there is nothing to wait for.
  const cacheKey = chatsCacheKey(workspaceId);
  const cached = getCachedChats(cacheKey);
  const [items, setItems] = useState<ConversationSummary[]>(cached ?? []);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(cached !== undefined);
  const [busyId, setBusyId] = useState<string | null>(null);
  // Collapsed is a per-viewer CONVENIENCE, so it lives in localStorage rather
  // than on the server: it is not content, nobody else needs to see it, and
  // losing it costs one click. Read lazily so the first paint is already in
  // the remembered state -- initialising to `false` and correcting in an
  // effect would make the list flash open on every navigation, and this
  // component remounts on each one (`AppShell` is rendered per page).
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(COLLAPSED_KEY) === "1";
    } catch {
      // Private window, blocked site data, or SSR: an unreadable preference
      // is not a collapsed rail. Default to showing the list.
      return false;
    }
  });

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        /* storage blocked -- the toggle still works for this session */
      }
      return next;
    });
  }, []);

  const load = useCallback(() => {
    api
      .listConversations(workspaceId)
      .then(({ conversations }) => {
        setItems(conversations);
        setCachedChats(chatsCacheKey(workspaceId), conversations);
      })
      // A failed refetch keeps whatever is on screen. The list is navigation,
      // not content: emptying it because one poll failed would take away the
      // way back to a chat over a blip.
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
    // ALWAYS through the URL, including when already on the chat page. The
    // in-place branch that used to live here fired a window event instead, so
    // switching chats left the address bar reading "/chat" — no back button
    // between conversations, nothing to bookmark or paste to a colleague, and
    // a refresh that could not restore the thread. `?c=` is now the single
    // source of truth and the page's existing param effect opens it.
    //
    // `push`, not `replace`: moving from one chat to another IS somewhere you
    // came from, so Back should return you to it.
    router.push(`${targetChatPath}?c=${encodeURIComponent(id)}`);
  }

  async function handleDelete(id: string, title: string) {
    if (busyId) return;
    // Confirmed, because this cascades: the turns AND any file uploaded into
    // that chat go with it, and nothing restores them.
    if (
      !window.confirm(
        `Delete "${title}"? The messages and any files attached to it are removed for good.`,
      )
    ) {
      return;
    }
    setBusyId(id);
    try {
      await api.deleteConversation(id, workspaceId);
      setItems((prev) => {
        const next = prev.filter((c) => c.id !== id);
        // The cache is what a remount renders, so a delete that does not
        // reach it brings the row back the moment you change page.
        setCachedChats(chatsCacheKey(workspaceId), next);
        return next;
      });
      // Deleting the chat you are READING has to clear the transcript too --
      // otherwise the page goes on showing messages that no longer exist and
      // the next question posts to a conversation the server 404s.
      if (id === activeId) {
        try {
          sessionStorage.removeItem(`chat.conversation.${workspaceId ?? "org"}`);
        } catch {
          /* storage blocked */
        }
        setActiveId(null);
        if (isCurrentChatPath) {
          window.dispatchEvent(new CustomEvent("new-chat"));
        } else {
          router.push(targetChatPath);
        }
      }
    } catch {
      // A failed delete must not remove the row: the chat is still there, and
      // a list that disagrees with the server is worse than an unchanged one.
      window.alert("Couldn't delete that chat. Try again.");
    } finally {
      setBusyId(null);
    }
  }

  const top5 = items.slice(0, 5);

  if (!loaded || top5.length === 0) {
    return null;
  }

  return (
    <div className="rail-chats" aria-label="Recent chats">
      {/* No "New" control here. Starting a chat lives on the chat page's own
          header, in the place you are already looking when you want one —
          this rail had a third button for that same action, beside an "Ask"
          row that also did it. The rail's job is getting BACK to a chat. */}
      {/* The whole header row is the collapse control, not just the arrow:
          a 12px chevron is a small target in a narrow rail, and the title and
          count are the obvious things to click when you want the section out
          of the way. The arrow is what SAYS it collapses -- it points down
          when open and right when shut, so the shape alone tells you which
          state you are in without reading the list. */}
      <div className="rail-chats-head">
        <button
          type="button"
          className="rail-chats-toggle"
          onClick={toggleCollapsed}
          aria-expanded={!collapsed}
          // Named only while the list exists: aria-controls pointing at an
          // unmounted node is a broken reference, not a hint.
          aria-controls={collapsed ? undefined : "rail-chats-list"}
          title={collapsed ? "Show recent chats" : "Hide recent chats"}
        >
          <span
            className={`rail-chats-chevron${collapsed ? " is-collapsed" : ""}`}
            aria-hidden
          >
            <ChevronIco />
          </span>
          <span className="rail-chats-title">Recent Chats</span>
          {/* The count stays visible while collapsed -- otherwise the section
              shrinks to a bare label that says nothing about whether there is
              anything behind it. */}
          <span className="rail-chats-badge">{top5.length}</span>
        </button>
      </div>

      {/* Unmounted rather than hidden: a collapsed list is not scrolled past,
          tabbed into or read by a screen reader, and `.rail-chats-list` is a
          `max-height` scroll container that would otherwise still occupy the
          rail's scroll calculations. */}
      {!collapsed && (
        <ul className="rail-chats-list" id="rail-chats-list" role="list">
          {top5.map((c) => {
            const isActive = isCurrentChatPath && c.id === activeId;
            const timeLabel = formatRelativeTime(c.last_activity_at || c.created_at);
            const title = c.title || "Untitled chat";

            return (
              <li key={c.id} className="rail-chat-row">
                {/* Two SIBLING buttons, not a button inside a button: nesting
                    them is invalid HTML and the inner one stops being
                    independently clickable. */}
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
                <button
                  type="button"
                  className="rail-chat-del"
                  onClick={() => handleDelete(c.id, title)}
                  disabled={busyId === c.id}
                  title={`Delete ${title}`}
                  aria-label={`Delete ${title}`}
                >
                  <TrashIco />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
