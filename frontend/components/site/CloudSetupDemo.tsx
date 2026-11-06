"use client";

import { useEffect, useState } from "react";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";

const STEPS = ["Request access", "Connect your tools", "Invite your team"];
const STEP_MS = 3800;

const TOOLS: Array<[BrandName, string]> = [
  ["notion", "Notion"],
  ["drive", "Google Drive"],
  ["slack", "Slack"],
  ["linear", "Linear"],
  ["github", "GitHub"],
];

/**
 * Handbook Cloud setup as three small screens: the real request form, the
 * tools connecting, the invites going out. Plays on its own until a step is
 * picked; still for reduced motion. Panes share one grid cell, so the card
 * never changes height.
 */
export function CloudSetupDemo() {
  const [at, setAt] = useState(0);
  const [auto, setAuto] = useState(true);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) setAuto(false);
  }, []);
  useEffect(() => {
    if (!auto) return;
    const id = window.setTimeout(() => setAt((i) => (i + 1) % STEPS.length), STEP_MS);
    return () => window.clearTimeout(id);
  }, [at, auto]);

  return (
    <div className="dcs">
      <div className="dcs-tabs" role="tablist" aria-label="Cloud setup">
        {STEPS.map((s, i) => (
          <button
            key={s}
            type="button"
            role="tab"
            aria-selected={i === at}
            className={i < at ? "is-done" : ""}
            onClick={() => { setAt(i); setAuto(false); }}
          >
            <span>{i + 1}</span>{s}
          </button>
        ))}
      </div>

      <div className="dcs-stack" aria-hidden>
        <div className={at === 0 ? "dcs-pane is-on" : "dcs-pane"}>
          <b>Request access for your company</b>
          <label>Company name<i>Acme Inc.</i></label>
          <label>Your work email<i>you@acme.com</i></label>
          <span className="dcs-btn">Request access</span>
          <small className="dcs-ok">Request received. We&rsquo;ll email you once it&rsquo;s approved.</small>
        </div>

        <div className={at === 1 ? "dcs-pane is-on" : "dcs-pane"}>
          <b>Sources</b>
          <ul className="dcs-tools">
            {TOOLS.map(([t, name], i) => (
              <li key={t} style={{ ["--i" as string]: i }}>
                <i><BrandGlyph name={t} size={14} /></i>
                {name}
                <em>Connected</em>
              </li>
            ))}
          </ul>
        </div>

        <div className={at === 2 ? "dcs-pane is-on" : "dcs-pane"}>
          <b>Invite your team</b>
          <div className="dcs-chips">
            {["priya@acme.com", "sam@acme.com", "lee@acme.com"].map((e, i) => (
              <span key={e} style={{ ["--i" as string]: i }}>{e}</span>
            ))}
          </div>
          <span className="dcs-btn">Send invites</span>
          <small className="dcs-ok">3 invites sent. They sign in with a one-time link.</small>
        </div>
      </div>
    </div>
  );
}
