"use client";

import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, Notification } from "@/lib/api";
import { formatReauthReason } from "@/lib/reauthReason";

/**
 * What needs this person's attention, on every page of the app.
 *
 * An expired token stops a sync silently: the card on Sources already said so,
 * but nobody opens a page that has never given them a reason to, so a space
 * went on answering from a corpus that had quietly stopped updating. The bell
 * is the reason to look.
 *
 * Derived, never stored (`GET /notifications`): an item disappears the moment
 * the connection is fixed, so there is no read/unread state that can disagree
 * with reality. Silent on failure -- a broken bell must never be the thing
 * that breaks every page it sits on.
 */
export function NotificationBell() {
  const [items, setItems] = useState<Notification[]>([]);
  const [open, setOpen] = useState(false);
  const [mounted, setMounted] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // Measured screen position for the panel. Portaled directly to document.body
  // so it reliably escapes `.app-rail`'s `transform` and `overflow-y: auto`,
  // floating cleanly above the whole page rather than being clipped or trapped
  // behind other layers.
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    setMounted(true);
  }, []);

  const place = useCallback(() => {
    const r = btn.current?.getBoundingClientRect();
    if (!r) return;
    const width = Math.min(352, window.innerWidth - 24);
    setAt({
      top: Math.min(r.bottom + 8, window.innerHeight - 24),
      // Opens to the button's right, then flips back inside the viewport --
      // the rail is on the left on desktop but the panel must not run off a
      // narrow screen.
      left: Math.max(12, Math.min(r.left, window.innerWidth - width - 12)),
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    place();
    // Recomputed rather than remembered: the rail scrolls, and a panel pinned
    // to a stale rect detaches from its own button.
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, place]);

  useEffect(() => {
    let live = true;
    api
      .notifications()
      .then((r) => live && setItems(r.items))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    function away(e: MouseEvent) {
      const target = e.target as Node;
      if (root.current?.contains(target) || panelRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    }
    function esc(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  const urgent = items.some((i) => i.severity === "high");

  return (
    <div className="bell" ref={root}>
      <button
        ref={btn}
        type="button"
        className="bell-btn"
        aria-expanded={open}
        aria-label={
          items.length ? `${items.length} things need attention` : "Nothing needs attention"
        }
        onClick={() => setOpen((o) => !o)}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path
            d="M6 9a6 6 0 1 1 12 0c0 3.2.7 4.9 1.5 5.9.4.5 0 1.1-.6 1.1H5.1c-.6 0-1-.6-.6-1.1C5.3 13.9 6 12.2 6 9Z"
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinejoin="round"
          />
          <path d="M10 19a2 2 0 0 0 4 0" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" />
        </svg>
        {items.length > 0 && (
          <span className="bell-count" data-urgent={urgent}>
            {items.length}
          </span>
        )}
      </button>

      {open && at && mounted && typeof document !== "undefined" && createPortal(
        <div
          ref={panelRef}
          className="bell-panel"
          role="dialog"
          aria-label="Needs attention"
          style={{ top: at.top, left: at.left }}
        >
          <div className="bell-head-row">
            <p className="bell-head">Needs attention</p>
            <button
              type="button"
              className="bell-close-btn"
              onClick={() => setOpen(false)}
              aria-label="Close notifications"
            >
              ×
            </button>
          </div>
          {items.length === 0 ? (
            /* Not an empty box: "nothing is broken" is the answer they opened
               this for, and it has to be stated to be believed. */
            <p className="bell-empty">Everything is connected and syncing.</p>
          ) : (
            <ul className="bell-list">
              {items.map((item, i) => (
                <li key={`${item.scope}-${item.provider}-${i}`} className="bell-item">
                  <span className="bell-dot" data-severity={item.severity} aria-hidden />
                  <div className="bell-item-copy">
                    <span className="bell-item-title">{item.title}</span>
                    <span className="bell-item-scope">{item.scope}</span>
                    <span className="bell-item-detail">
                      {item.kind === "reauth" ? formatReauthReason(item.provider, item.detail) : item.detail}
                    </span>
                    <Link href={item.href} className="bell-item-action" onClick={() => setOpen(false)}>
                      {item.action}
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>,
        document.body
      )}
    </div>
  );
}
