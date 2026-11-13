"use client";

/**
 * "A new version is ready" when the server's /build-id changes while this tab
 * is open (polled, and on focus). The baseline is the server's first answer,
 * never the id baked into the page, or a cached page shows the pill forever.
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
