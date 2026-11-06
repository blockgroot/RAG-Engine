"use client";

import { useEffect, useState } from "react";

/* The real step labels from Admin › Updates (app/admin/updates/page.tsx). */
const STEPS = [
  "Backing up the database",
  "Downloading the new version",
  "Restarting Handbook",
  "Waiting for Handbook to start",
];
const STEP_MS = 1400;

/**
 * A copy of the self-hosted Updates page: the offer, the button, then the
 * real steps one by one, then done; and over again. Still for reduced motion.
 */
export function UpdateDemo() {
  // 0 = offer, 1..STEPS.length = running, STEPS.length + 1 = done
  const [at, setAt] = useState(0);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const id = window.setInterval(() => setAt((i) => (i + 1) % (STEPS.length + 3)), STEP_MS);
    return () => window.clearInterval(id);
  }, []);

  const done = at > STEPS.length;
  const running = at >= 1 && !done;

  return (
    <div className="dupd" aria-hidden>
      <div className="dupd-bar"><span>Admin</span> › <b>Updates</b></div>
      <div className="dupd-body">
        <small>You are on Handbook 1.3.2</small>
        <b className="dupd-title">{done ? "Handbook is now on 1.4.0" : "Handbook 1.4.0 is available"}</b>
        <ul className="dupd-notes">
          <li>Faster answers from large spreadsheets</li>
          <li>Security fixes</li>
        </ul>
        {at === 0 && <span className="dupd-btn">Update to 1.4.0</span>}
        {(running || done) && (
          <ol className="dupd-steps">
            {STEPS.map((s, i) => {
              const state = done || i + 1 < at ? "is-done" : i + 1 === at ? "is-now" : "";
              return <li key={s} className={state}>{s}</li>;
            })}
          </ol>
        )}
      </div>
    </div>
  );
}
