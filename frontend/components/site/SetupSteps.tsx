"use client";

import { useEffect, useState } from "react";

const STEPS: Array<[string, string]> = [
  ["Connect your tools", "Notion, Drive, Slack, Linear or GitHub. Choose what Handbook may read."],
  ["Invite your team", "By email. People sign in with a secure link, so there is no password to manage."],
  ["Add spaces, if you need them", "A project or department gets its own members and tools, kept apart from the rest."],
  ["Pick a model, or use ours", "Use the built-in model or your company’s own provider. The connection is tested first."],
];

const STEP_MS = 1800;

/**
 * Section 9: the setup as an open timeline instead of four more cards. A line
 * fills from step to step and each number lights as it is reached, then the
 * run starts over. Reduced motion shows every step done and never moves.
 */
export function SetupSteps() {
  const [at, setAt] = useState(0);
  const [still, setStill] = useState(false);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setStill(true);
      return;
    }
    // One extra beat on the last step before starting over.
    const id = window.setInterval(() => setAt((i) => (i + 1) % (STEPS.length + 1)), STEP_MS);
    return () => window.clearInterval(id);
  }, []);

  const reached = still ? STEPS.length - 1 : Math.min(at, STEPS.length - 1);

  return (
    <ol
      className="setup-steps"
      style={{ ["--fill" as string]: reached / (STEPS.length - 1) }}
    >
      {STEPS.map(([title, text], i) => (
        <li key={title} className={i <= reached ? "is-done" : ""} aria-current={i === reached ? "step" : undefined}>
          <span className="setup-dot">{String(i + 1).padStart(2, "0")}</span>
          <strong>{title}</strong>
          <p>{text}</p>
        </li>
      ))}
    </ol>
  );
}
