"use client";

/**
 * "A new version is ready": the tab was loaded from an older RELEASE than the
 * one now live (the build id is the newest release, next.config.js; an ordinary
 * deploy of main keeps it). Checked every few minutes and when the tab comes
 * back into view; Refresh loads the new version.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

const OWN_BUILD = process.env.NEXT_PUBLIC_BUILD_ID;
const EVERY_MS = 5 * 60 * 1000;

export function NewVersionPill({ admin }: { admin: boolean }) {
  const [ready, setReady] = useState(false);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    if (!OWN_BUILD) return;
    let stop = false;
    const check = () => {
      if (stop || document.visibilityState !== "visible") return;
      fetch("/build-id", { cache: "no-store" })
        .then((r) => (r.ok ? r.json() : null))
        .then((d: { id?: string | null } | null) => {
          if (!stop && d?.id && d.id !== OWN_BUILD) setReady(true);
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
