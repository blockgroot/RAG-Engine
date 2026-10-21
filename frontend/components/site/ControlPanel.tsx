"use client";

import { useState, type ReactNode } from "react";
import { BrandGlyph } from "@/components/BrandGlyph";
import { FeatureIcon, type FeatureIconName } from "@/components/FeatureIcon";

const ROWS: Array<{ title: string; icon: FeatureIconName; text: string; fig: ReactNode }> = [
  {
    title: "Your AI model",
    icon: "model",
    text: "Use Handbook’s built-in model or connect a provider your company already uses. The connection is tested before it is saved.",
    fig: (
      <div className="ctl-list">
        <span className="is-on">Handbook&rsquo;s model <i>default</i></span>
        <span>Your company&rsquo;s provider key <i>connection tested</i></span>
      </div>
    ),
  },
  {
    title: "Spaces for teams",
    icon: "workspace",
    text: "Give a project or department its own members and tools. A space sees only its own documents, and people outside it cannot open it.",
    fig: (
      <div className="ctl-spaces">
        <span><b>Company-wide</b><i>everyone</i></span>
        <span className="is-on"><b>Platform team</b><i>6 members &middot; Linear, GitHub</i></span>
        <span><b>Hiring</b><i>3 members &middot; Drive</i></span>
      </div>
    ),
  },
  {
    title: "Approval for every action",
    icon: "secure",
    text: "Agent mode only drafts. The person who asked reviews the words and the audience, approves, and it is sent from their own connected account.",
    fig: (
      <ol className="ctl-flow">
        <li>Draft</li>
        <li>You review</li>
        <li className="is-on">You approve</li>
        <li>Sent as you</li>
      </ol>
    ),
  },
  {
    title: "Admin switches",
    icon: "private",
    text: "Admins decide whether Agent mode is on, where it works, and which extra tools it may use. They see the switches, never what someone drafted.",
    fig: (
      <div className="ctl-list">
        <span className="is-on">Agent mode <i>on</i></span>
        <span className="is-on"><BrandGlyph name="slack" size={12} /> Agent in Slack <i>on</i></span>
        <span>Extra tools added by an admin <i>each off until chosen</i></span>
      </div>
    ),
  },
];

/** Section 6: rows on the left, the open row's detail and picture on the right. */
export function ControlPanel() {
  const [open, setOpen] = useState(0);
  const row = ROWS[open];
  return (
    <div className="ctl">
      <div className="ctl-rows" role="tablist" aria-label="Controls" aria-orientation="vertical">
        {ROWS.map((r, i) => (
          <button
            key={r.title}
            type="button"
            role="tab"
            aria-selected={i === open}
            aria-controls="ctl-detail"
            className={i === open ? "is-on" : ""}
            onClick={() => setOpen(i)}
          >
            <span>{r.title}</span>
            <FeatureIcon name={r.icon} />
          </button>
        ))}
      </div>
      <div className="ctl-detail" id="ctl-detail" role="tabpanel" aria-label={row.title}>
        <div className="site-frame ctl-fig" aria-hidden>{row.fig}</div>
        <p>{row.text}</p>
      </div>
    </div>
  );
}
