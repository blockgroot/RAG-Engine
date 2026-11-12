"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
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

function MoreIco() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <circle cx="5" cy="12" r="1.8" />
      <circle cx="12" cy="12" r="1.8" />
      <circle cx="19" cy="12" r="1.8" />
    </svg>
  );
}

function PinIco({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M9 4h6l-1 5 3 3v2H7v-2l3-3-1-5ZM12 14v6"
        stroke="currentColor"
        strokeWidth="1.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function PenIco() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M4 20h4L19 9l-4-4L4 16v4ZM13.5 6.5l4 4"
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
  // The ⋯ menu, portaled to <body> like the bell: the rail's transform and
  // scrolling would clip a menu drawn inside it.
  const [menu, setMenu] = useState<{ id: string; top: number; left: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
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

  useEffect(() => {
    if (!menu) return;
    const away = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      if (!menuRef.current?.contains(t) && !t.closest?.(".rail-chat-more")) setMenu(null);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setMenu(null);
    const close = () => setMenu(null);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
      window.removeEventListener("resize", close);
    };
  }, [menu]);

  function openMenu(id: string, button: HTMLElement) {
    if (menu?.id === id) return setMenu(null);
    const r = button.getBoundingClientRect();
    const width = 176;
    setMenu({
      id,
      top: Math.min(r.bottom + 4, window.innerHeight - 140),
      left: Math.min(r.right - width + 8, window.innerWidth - width - 8),
    });
  }

  // Shown at once, saved behind; a failed save reloads what the server has.
  async function change(id: string, patch: { title?: string | null; pinned?: boolean }) {
    setItems((prev) => {
      const next = prev.map((c) =>
        c.id !== id
          ? c
          : {
              ...c,
              ...(patch.pinned !== undefined && { pinned: patch.pinned }),
              ...(patch.title !== undefined && patch.title && { title: patch.title, renamed: true }),
            },
      );
      next.sort((a, b) => Number(Boolean(b.pinned)) - Number(Boolean(a.pinned)));
      return next;
    });
    try {
      await api.updateConversation(id, patch, workspaceId);
    } catch {
      window.alert("Couldn't save that change. Try again.");
    }
    load();
  }

  function startRename(c: ConversationSummary) {
    setMenu(null);
    setDraft(c.title ?? "");
    setEditingId(c.id);
  }

  function finishRename(c: ConversationSummary, save: boolean) {
    setEditingId(null);
    const title = draft.trim();
    if (!save || title === (c.title ?? "")) return;
    // An emptied title goes back to the first question.
    change(c.id, { title: title || null });
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
              <li key={c.id} className={`rail-chat-row${menu?.id === c.id ? " is-menu" : ""}`}>
                {editingId === c.id ? (
                  <input
                    className="rail-chat-rename"
                    value={draft}
                    autoFocus
                    maxLength={120}
                    aria-label="Chat name"
                    placeholder="Name this chat"
                    onChange={(e) => setDraft(e.target.value)}
                    onFocus={(e) => e.currentTarget.select()}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") finishRename(c, true);
                      if (e.key === "Escape") finishRename(c, false);
                    }}
                    onBlur={() => finishRename(c, true)}
                  />
                ) : (
                  <>
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
                      {c.pinned && (
                        <span className="rail-chat-pin" aria-label="Pinned">
                          <PinIco size={11} />
                        </span>
                      )}
                      <span className="rail-chat-label">{title}</span>
                      {timeLabel && <span className="rail-chat-time">{timeLabel}</span>}
                    </button>
                    <button
                      type="button"
                      className="rail-chat-more"
                      onClick={(e) => openMenu(c.id, e.currentTarget)}
                      disabled={busyId === c.id}
                      aria-haspopup="menu"
                      aria-expanded={menu?.id === c.id}
                      title="More"
                      aria-label={`More for ${title}`}
                    >
                      <MoreIco />
                    </button>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {menu &&
        (() => {
          const c = items.find((x) => x.id === menu.id);
          if (!c) return null;
          const title = c.title || "Untitled chat";
          return createPortal(
            <div
              ref={menuRef}
              className="rail-chat-menu"
              role="menu"
              aria-label={`Options for ${title}`}
              style={{ top: menu.top, left: menu.left }}
            >
              <button type="button" role="menuitem" onClick={() => startRename(c)}>
                <PenIco /> Rename
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setMenu(null);
                  change(c.id, { pinned: !c.pinned });
                }}
              >
                <PinIco /> {c.pinned ? "Unpin" : "Pin to top"}
              </button>
              <hr />
              <button
                type="button"
                role="menuitem"
                className="is-danger"
                onClick={() => {
                  setMenu(null);
                  handleDelete(c.id, title);
                }}
              >
                <TrashIco /> Delete
              </button>
            </div>,
            document.body,
          );
        })()}
    </div>
  );
}
