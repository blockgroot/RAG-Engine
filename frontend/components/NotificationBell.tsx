"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, Notification } from "@/lib/api";

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
  const root = useRef<HTMLDivElement>(null);

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
      if (!root.current?.contains(e.target as Node)) setOpen(false);
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

      {open && (
        <div className="bell-panel" role="dialog" aria-label="Needs attention">
          <p className="bell-head">Needs attention</p>
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
                    <span className="bell-item-detail">{item.detail}</span>
                    <Link href={item.href} className="bell-item-action" onClick={() => setOpen(false)}>
                      {item.action}
                    </Link>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
