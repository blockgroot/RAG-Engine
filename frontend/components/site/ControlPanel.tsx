"use client";

import { useState, type ReactNode } from "react";
import { BrandGlyph } from "@/components/BrandGlyph";
import { FeatureIcon, type FeatureIconName } from "@/components/FeatureIcon";

/** A switch as the admin pages draw it. Decorative: the whole figure is aria-hidden. */
const Toggle = ({ on = true }: { on?: boolean }) => <i className={on ? "cf-tog" : "cf-tog is-off"} />;
const Check = () => (
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3.5 8.5l3 3 6-7" />
  </svg>
);

// Each figure is a miniature of the real admin page it stands for (docs/poc-images:
// company-model, spaces, the Agent card, action-permissions), with no vendor names.
const ROWS: Array<{ title: string; icon: FeatureIconName; text: string; fig: ReactNode }> = [
  {
    title: "Your AI model",
    icon: "model",
    text: "Use Handbook’s built-in model or connect a provider your company already uses. The connection is tested before it is saved.",
    fig: (
      <div className="cf-card">
        <p className="cf-eyebrow">Model setup</p>
        <p className="cf-title">Connect your model</p>
        <div className="cf-fields">
          <span><b>1</b>Provider<em>Your provider <small>&#9662;</small></em></span>
          <span><b>2</b>Model ID<em>your-model-id</em></span>
          <span><b>3</b>API key<em>&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull; 4f2a</em></span>
        </div>
        <div className="cf-foot">
          <span className="cf-ok"><Check /> Test passed &middot; key encrypted</span>
          <b className="cf-btn">Test and connect</b>
        </div>
        <div className="cf-split">
          <span><small>Chat answers</small>Your model</span>
          <span><small>Indexing</small>Handbook&rsquo;s model</span>
        </div>
      </div>
    ),
  },
  {
    title: "Spaces for teams",
    icon: "workspace",
    text: "Give a project or department its own members and tools. A space sees only its own documents, and people outside it cannot open it.",
    fig: (
      <div className="cf-card">
        <p className="cf-eyebrow">Spaces</p>
        <p className="cf-title">Your team spaces</p>
        <div className="cf-spaces">
          <span>
            <b className="cf-av">P</b>
            <span><strong>Platform team</strong><small>6 members &middot; <BrandGlyph name="linear" size={10} /> <BrandGlyph name="github" size={10} /></small></span>
            <em>Owner</em>
          </span>
          <span>
            <b className="cf-av is-b">L</b>
            <span><strong>Launch notes</strong><small>4 members &middot; <BrandGlyph name="notion" size={10} /> <BrandGlyph name="slack" size={10} /></small></span>
            <em>Member</em>
          </span>
          <span className="is-locked">
            <b className="cf-av is-c">H</b>
            <span><strong>Hiring</strong><small>3 members &middot; <BrandGlyph name="drive" size={10} /></small></span>
            <em>No access</em>
          </span>
        </div>
      </div>
    ),
  },
  {
    title: "Approval for every action",
    icon: "secure",
    text: "Agent mode only drafts. The person who asked reviews the words and the audience, approves, and it is sent from their own connected account.",
    fig: (
      <div className="cf-card">
        <ol className="cf-steps">
          <li className="is-done">Draft</li>
          <li className="is-done">Review</li>
          <li className="is-now">Approve</li>
          <li>Sent as you</li>
        </ol>
        <div className="cf-draft">
          <div className="cf-draft-head">
            <strong>Slack: Post message</strong>
            <small>as you, in Slack</small>
          </div>
          <dl>
            <dt>Where</dt><dd><BrandGlyph name="slack" size={10} /> #launch</dd>
            <dt>Message</dt><dd>Checkout retry ships Thursday. Release notes are in the launch doc.</dd>
          </dl>
          <div className="cf-draft-btns">
            <b>Approve</b><i>Edit</i><i>Cancel</i><small>Expires in 14:50</small>
          </div>
        </div>
      </div>
    ),
  },
  {
    title: "Admin switches",
    icon: "private",
    text: "Admins decide whether Agent mode is on, where it works, and which extra tools it may use. They see the switches, never what someone drafted.",
    fig: (
      <div className="cf-card">
        <p className="cf-eyebrow">Action permissions</p>
        <div className="cf-radio">
          <span><i />Off<small>Handbook only reads</small></span>
          <span className="is-on"><i />On<small>Each write approved</small></span>
        </div>
        <div className="cf-tools">
          <span><BrandGlyph name="linear" size={14} /><b>Linear<small>12 of 68 tools on</small></b><Toggle /></span>
          <span><BrandGlyph name="github" size={14} /><b>GitHub<small>14 of 46 tools on</small></b><Toggle /></span>
          <span><BrandGlyph name="notion" size={14} /><b>Notion<small>0 of 65 tools on</small></b><Toggle on={false} /></span>
        </div>
      </div>
    ),
  },
];

/** Section 6: rows on the left, the open row's detail and picture on the right. */
export function ControlPanel() {
  const [open, setOpen] = useState(0);
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
      {/* Every row's picture and text sit in one grid cell and only the open
          one is visible, so the panel is always the size of the largest and
          does not jump between tabs. */}
      <div className="ctl-detail" id="ctl-detail" role="tabpanel" aria-label={ROWS[open].title}>
        {ROWS.map((r, i) => (
          <div key={r.title} className={i === open ? "ctl-pane is-on" : "ctl-pane"} aria-hidden={i !== open}>
            <div className="site-frame ctl-fig" aria-hidden>{r.fig}</div>
            <p>{r.text}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
