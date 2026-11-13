"use client";

/**
 * "A new version is ready": a release went live while this tab was open. The
 * server's /build-id (the newest release it carries, next.config.js; an
 * ordinary deploy of main keeps it) is read when the tab opens and again every
 * few minutes and when the tab comes back into view; the pill shows when it
 * changes, and Refresh loads the new version.
 *
 * The first answer is the baseline, never the id baked into this page: a
 * reload that still got an older page (a cached copy, a deploy rolling out)
 * showed the pill again straight after Refresh, and nothing the person could
 * do would clear it.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

const EVERY_MS = 5 * 60 * 1000;

export function NewVersionPill({ admin }: { admin: boolean }) {
  const [ready, setReady] = useState(false);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    let stop = false;
    let first: string | null = null;
    const check = () => {
      if (stop || document.visibilityState !== "visible") return;
      fetch("/build-id", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: { id?: string | null } | null) => {
          if (stop || !d?.id) return;
          if (first === null) first = d.id;
          else if (d.id !== first) setReady(true);
        })
        .catch(() => {});
    };
    const timer = setInterval(check, EVERY_MS);
    document.addEventListener("visibilitychange", check);
    check();
    return () => {
      stop = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", check);
    };
  }, []);

  if (!ready || hidden) return null;
  return (
    <div className="version-pill" role="status">
      <span className="version-pill-spark" aria-hidden>✦</span>
      <span>A new version of Handbook is ready</span>
      {admin && (
        <Link className="version-pill-link" href="/admin/updates">
          What&apos;s new
        </Link>
      )}
      <button type="button" className="version-pill-go" onClick={() => window.location.reload()}>
        Refresh
      </button>
      <button type="button" className="version-pill-close" aria-label="Later" onClick={() => setHidden(true)}>
        ×
      </button>
    </div>
  );
}
