"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const DISMISSED_KEY = "release.dismissed";

/**
 * A new Handbook release, next to the mode switch, for admins (the release
 * feed is `GET /admin/updates`, admin only). Self-hosted: "X is available",
 * which they install. Cloud: "New in X" for a week after it ships. Hidden per
 * version in this browser once dismissed; a newer version shows again. Silent
 * on failure, like the bell.
 */
export function ReleaseChip({ admin }: { admin: boolean }) {
  const [chip, setChip] = useState<{ version: string; badge: string; text: string; title: string; urgent: boolean } | null>(null);

  useEffect(() => {
    if (!admin) return;
    let live = true;
    api
      .getUpdates()
      .then((u) => {
        if (!live) return;
        const security = u.newer.some((r) => r.security);
        const next = u.latest
          ? { version: u.latest, badge: "Update", text: security ? `Security fix ${u.latest}` : `${u.latest} is out`, title: security ? `Handbook ${u.latest} fixes a security problem. Update soon.` : `Handbook ${u.latest} is ready to install`, urgent: security }
          : u.whats_new
            ? { version: u.whats_new.version, badge: `What's new in ${u.whats_new.version}`, text: "", title: `See what's new in Handbook ${u.whats_new.version}`, urgent: false }
            : null;
        let dismissed: string | null = null;
        try {
          dismissed = localStorage.getItem(DISMISSED_KEY);
        } catch {
          /* private window */
        }
        setChip(next && next.version !== dismissed ? next : null);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [admin]);

  if (!chip) return null;
  return (
    <span className="release-chip" data-urgent={chip.urgent || undefined}>
      <Link href="/admin/updates" className="release-chip-link" title={chip.title}>
        <i className="release-chip-dot" aria-hidden />
        {chip.text && (
          <>
            <span className="release-chip-text">{chip.text}</span>
            <span className="release-chip-sep" aria-hidden>·</span>
          </>
        )}
        <span className="release-chip-cta">{chip.badge}</span>
      </Link>
      <button
        type="button"
        className="release-chip-x"
        aria-label="Dismiss"
        title="Dismiss"
        onClick={() => {
          try {
            localStorage.setItem(DISMISSED_KEY, chip.version);
          } catch {
            /* private window: hidden for this page view only */
          }
          setChip(null);
        }}
      >
        <svg viewBox="0 0 20 20" width="12" height="12" fill="none" aria-hidden>
          <path d="m6 6 8 8m0-8-8 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </svg>
      </button>
    </span>
  );
}
