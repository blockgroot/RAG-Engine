"use client";

import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api, Notification } from "@/lib/api";
import { formatReauthReason } from "@/lib/reauthReason";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

const BRANDS: Record<string, BrandName> = {
  google: "drive",
  slack: "slack",
  notion: "notion",
  linear: "linear",
  github: "github",
};

/** An item with no tool behind it (a question gap, a new version). */
function KindIcon({ kind }: { kind: Notification["kind"] }) {
  const d =
    kind === "update"
      ? "M10 15V5m0 0-4 4m4-4 4 4"
      : kind === "gap"
        ? "M7.5 7.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.7M10 14.5v.01"
        : "M10 6v4.5M10 14v.01";
  return (
    <svg viewBox="0 0 20 20" width="16" height="16" fill="none">
      <path d={d} stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

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
 *
 * Dismissing hides an item in this browser only, keyed by its whole text: the
 * same problem stays hidden, a changed or new one shows again. Sources still
 * shows the problem itself.
 */
const DISMISSED_KEY = "bell.dismissed";
const keyOf = (n: Notification) => [n.kind, n.provider, n.scope, n.title, n.detail].join("\u241f");

function readDismissed(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(DISMISSED_KEY) ?? "[]"));
  } catch {
    return new Set();
  }
}

export function NotificationBell() {
  const [all, setAll] = useState<Notification[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const items = all.filter((n) => !dismissed.has(keyOf(n)));

  function dismiss(keys: string[]) {
    // Only keys still current are kept, so the stored list never grows past
    // what the bell shows.
    const live = new Set(all.map(keyOf));
    const next = new Set([...dismissed, ...keys].filter((k) => live.has(k)));
    setDismissed(next);
    try {
      localStorage.setItem(DISMISSED_KEY, JSON.stringify([...next]));
    } catch {
      /* private window: dismissed for this page view only */
    }
  }
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
      .then((r) => {
        if (!live) return;
        setDismissed(readDismissed());
        setAll(r.items);
      })
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
            <p className="bell-head">
              Notifications
              {items.length > 0 && <span className="bell-head-count">{items.length}</span>}
            </p>
            {items.length > 1 && (
              <button type="button" className="bell-clear" onClick={() => dismiss(items.map(keyOf))}>
                Clear all
              </button>
            )}
            <button
              type="button"
              className="bell-close-btn"
              onClick={() => setOpen(false)}
              aria-label="Close notifications"
            >
              <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
                <path d="m5.5 5.5 9 9m0-9-9 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          {items.length === 0 ? (
            /* Not an empty box: "nothing is broken" is the answer they opened
               this for, and it has to be stated to be believed. */
            <div className="bell-empty">
              <span className="bell-empty-mark" aria-hidden>
                <svg viewBox="0 0 20 20" fill="none"><path d="m5.5 10.5 3 3 6-7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </span>
              <p>
                <strong>You&rsquo;re all caught up</strong>
                {all.length ? "Dismissed items still show on Sources." : "Everything is connected and syncing."}
              </p>
            </div>
          ) : (
            <ul className="bell-list">
              {items.map((item) => {
                const brand = BRANDS[item.provider];
                const action = /^https?:\/\//.test(item.href) ? (
                  <a href={item.href} target="_blank" rel="noopener noreferrer"
                     className="bell-item-action" onClick={() => setOpen(false)}>
                    {item.action} <span aria-hidden>&rarr;</span>
                  </a>
                ) : (
                  <Link href={item.href} className="bell-item-action" onClick={() => setOpen(false)}>
                    {item.action} <span aria-hidden>&rarr;</span>
                  </Link>
                );
                return (
                  <li key={keyOf(item)} className="bell-item" data-severity={item.severity}>
                    <span className="bell-item-icon" aria-hidden>
                      {brand ? <BrandGlyph name={brand} size={18} /> : <KindIcon kind={item.kind} />}
                      <i className="bell-dot" data-severity={item.severity} />
                    </span>
                    <div className="bell-item-copy">
                      <span className="bell-item-title">{item.title}</span>
                      <span className="bell-item-detail">
                        {item.kind === "reauth" ? formatReauthReason(item.provider, item.detail) : item.detail}
                      </span>
                      <span className="bell-item-foot">
                        {action}
                        <span className="bell-item-scope">{item.scope}</span>
                      </span>
                    </div>
                    <button
                      type="button"
                      className="bell-dismiss"
                      onClick={() => dismiss([keyOf(item)])}
                      aria-label={`Dismiss: ${item.title}`}
                      title="Dismiss"
                    >
                      <svg viewBox="0 0 20 20" fill="none" aria-hidden="true">
                        <path d="m6 6 8 8m0-8-8 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                      </svg>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>,
        document.body
      )}
    </div>
  );
}
