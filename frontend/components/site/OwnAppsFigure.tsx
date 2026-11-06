"use client";

import { useEffect, useState } from "react";
import { BrandGlyph } from "@/components/BrandGlyph";

type App = "google" | "slack";

/* The in-app setup's steps (components/OwnAppSetup.tsx), each with one line of what to do. */
const APPS: Record<App, { name: string; source: string; steps: Array<[string, string]> }> = {
  google: {
    name: "Google app",
    source: "Google Drive",
    steps: [
      ["Create a Google Cloud project", "Sign in with your company account and name the project Handbook."],
      ["Turn on four Google services", "Drive, Docs, Sheets and Forms: open each and press Enable."],
      ["Keep the app inside your company", "Under Audience, choose Internal."],
      ["Create the sign-in key", "Add your two Handbook addresses as redirect URIs."],
      ["Paste the keys into Handbook", "Copy the Client ID and Client secret into Handbook."],
    ],
  },
  slack: {
    name: "Slack app",
    source: "Slack",
    steps: [
      ["Create the app in Slack", "Slack opens with everything filled in. Pick your workspace and press Create."],
      ["Paste the keys into Handbook", "Copy the three values from Basic Information › App Credentials."],
      ["Let Slack check it can reach Handbook", "Press Retry in Event Subscriptions. It says Verified."],
    ],
  },
};
const ORDER: App[] = ["google", "slack"];
const STEP_MS = 1700;

/**
 * A copy of Sources › Your company's apps. Each app's card opens its real
 * setup steps in the panel below; on its own it plays Google, then Slack.
 * Both step lists share one grid cell, so the panel never changes height.
 * Shows Google's finished steps, still, for reduced motion.
 */
export function OwnAppsFigure() {
  const [app, setApp] = useState<App>("google");
  const [at, setAt] = useState(0); // steps.length = done (held one beat)
  const [still, setStill] = useState(false);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setStill(true);
      setAt(APPS.google.steps.length);
    }
  }, []);
  useEffect(() => {
    if (still) return;
    const id = window.setTimeout(() => {
      if (at < APPS[app].steps.length) setAt(at + 1);
      else {
        setApp(ORDER[(ORDER.indexOf(app) + 1) % ORDER.length]);
        setAt(0);
      }
    }, at === APPS[app].steps.length ? STEP_MS * 1.6 : STEP_MS);
    return () => window.clearTimeout(id);
  }, [app, at, still]);

  const pick = (k: App) => {
    setApp(k);
    setAt(still ? APPS[k].steps.length : 0);
  };

  return (
    <div className="oaf">
      <span className="oaf-head">Sources &rsaquo; Your company&rsquo;s apps</span>

      {ORDER.map((k) => {
        const a = APPS[k];
        const on = k === app;
        const busy = on && at < a.steps.length;
        return (
          <button key={k} type="button" className={on ? "oaf-row is-on" : "oaf-row"} aria-pressed={on} onClick={() => pick(k)}>
            <i><BrandGlyph name={k} size={20} /></i>
            <span>
              <b>{a.name}</b>
              <small>{busy ? `Setting up: step ${at + 1} of ${a.steps.length}` : `${a.source} connects through this app.`}</small>
            </span>
            <em className={busy ? "is-busy" : "is-ready"}>{busy ? "Setting up" : "Ready"}</em>
          </button>
        );
      })}

      <div className="oaf-panel" aria-live="polite">
        {ORDER.map((k) => {
          const a = APPS[k];
          const on = k === app;
          const done = at >= a.steps.length;
          return (
            <div key={k} className={on ? "oaf-pane is-on" : "oaf-pane"} aria-hidden={!on}>
              <ol>
                {a.steps.map(([title], i) => (
                  <li key={title} className={on && (done || i < at) ? "is-done" : on && i === at ? "is-now" : ""}>
                    {title}
                  </li>
                ))}
              </ol>
              <p className="oaf-note" key={on ? at : -1}>
                {on && !done ? a.steps[at][1] : `Done. Now press Connect on the ${a.source} card.`}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
