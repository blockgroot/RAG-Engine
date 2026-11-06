"use client";

import { useEffect, useState } from "react";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { HbLogo } from "@/components/site/HbLogo";

const TOOLS: BrandName[] = ["notion", "drive", "slack", "linear", "github"];

const MODES = {
  cloud: {
    lead: "We host it, run it and update it. You connect your tools and invite your team.",
    points: ["No servers, nothing to install", "Updates and backups handled for you", "Your data kept apart from every other company"],
    run: "We run it",
    update: "Updates arrive by themselves",
    href: "#cloud",
    go: "How Cloud works",
  },
  self: {
    lead: "Handbook runs on your own servers or cloud account, with its own database.",
    points: ["Your data never leaves your company", "You choose where it lives and when to update", "Install it yourself, or we do it for you"],
    run: "You run it",
    update: "You update when you choose",
    href: "#self-hosted",
    go: "The full setup",
  },
} as const;

type Mode = keyof typeof MODES;

/**
 * The deployment page's opening: one switch and one picture. "Your company"
 * is a dashed boundary around the team and its tools. On Cloud, Handbook sits
 * outside it in our cloud; on Self-Hosted the boundary grows to take Handbook
 * in. Switches by itself until someone picks; still for reduced motion.
 */
export function DeployHero() {
  const [mode, setMode] = useState<Mode>("cloud");
  const [auto, setAuto] = useState(true);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) setAuto(false);
  }, []);
  useEffect(() => {
    if (!auto) return;
    const id = window.setTimeout(() => setMode((m) => (m === "cloud" ? "self" : "cloud")), 4500);
    return () => window.clearTimeout(id);
  }, [mode, auto]);

  const m = MODES[mode];
  const pick = (next: Mode) => {
    setMode(next);
    setAuto(false);
  };

  return (
    <div className={`dh is-${mode}`}>
      <div className="dh-copy">
        <p className="site-label">Deployment</p>
        <h1 className="sec-title">Two ways to run Handbook.</h1>
        <p className="sec-lead">Same Handbook, same answers. The difference is who runs it and where your data is kept.</p>

        <div className="dh-switch" role="tablist" aria-label="Deployment">
          {(Object.keys(MODES) as Mode[]).map((k) => (
            <button key={k} type="button" role="tab" aria-selected={mode === k} onClick={() => pick(k)}>
              {k === "cloud" ? "Cloud" : "Self-Hosted"}
            </button>
          ))}
          <i className="dh-switch-thumb" aria-hidden />
        </div>

        {/* Both details sit in one grid cell, so the column never changes height. */}
        <div className="dh-details">
          {(Object.keys(MODES) as Mode[]).map((k) => (
            <div key={k} className={k === mode ? "dh-detail is-on" : "dh-detail"} aria-hidden={k !== mode}>
              <p>{MODES[k].lead}</p>
              <ul>
                {MODES[k].points.map((p) => <li key={p}>{p}</li>)}
              </ul>
              <a href={MODES[k].href} className="dh-go" tabIndex={k === mode ? undefined : -1}>{MODES[k].go} &rarr;</a>
            </div>
          ))}
        </div>
      </div>

      <div className="dh-art" aria-hidden>
        <div className="dh-cloud">
          <span className="dh-zone-label">Our cloud</span>
        </div>
        <div className="dh-company">
          <span className="dh-zone-label">
            Your company
            <em>{mode === "self" ? " · your servers" : ""}</em>
          </span>
        </div>

        <div className="dh-team">
          <div className="dh-people">
            <i className="is-a">P</i><i className="is-b">S</i><i className="is-c">L</i>
          </div>
          <small>Your team</small>
          <div className="dh-tools">
            {TOOLS.map((t) => <i key={t}><BrandGlyph name={t} size={12} /></i>)}
          </div>
          <small>Your tools</small>
        </div>

        <div className="dh-wire"><em /><em /><em /></div>

        <div className="dh-node">
          <HbLogo className="dh-node-logo" />
          <b>Handbook</b>
          <span className="dh-db">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7" />
            </svg>
            Database
          </span>
          <small key={mode}>{m.run} &middot; {m.update}</small>
        </div>

        <p className="dh-note" key={`n-${mode}`}>
          {mode === "self" ? "Your data never leaves your company" : "Your data is kept apart from every other company"}
        </p>
      </div>
    </div>
  );
}
