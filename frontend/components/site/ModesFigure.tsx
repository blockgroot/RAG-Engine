"use client";

import { useEffect, useState, type ReactNode } from "react";
import { BrandGlyph } from "@/components/BrandGlyph";
import { ActIcon, AskIcon, ChartIcon, DeepIcon } from "@/components/ModeIcons";

/** Illustrative numbers for the Chart tab; drawn as plain bars, no chart library. */
const BARS: Array<[string, number]> = [
  ["Priya", 48],
  ["Sam", 41],
  ["Leah", 29],
  ["Omar", 22],
  ["Jun", 14],
];

const STEP_MS = 6000;

const MODES: Array<{
  label: string;
  icon: ReactNode;
  when: string;
  ask: string;
  trace: string;
  body: ReactNode;
}> = [
  {
    label: "Ask",
    icon: <AskIcon />,
    when: "For a fact or a quick answer.",
    ask: "Who reviewed the checkout change?",
    trace: "Read 1 pull request live from GitHub",
    body: (
      <>
        <p className="mode-text">
          <b>Leah</b> reviewed and approved it on Tuesday. Sam asked for a retry
          on failed card payments, which was added before the merge.
        </p>
        <div className="mode-chips">
          <span><BrandGlyph name="github" size={12} /> checkout#218 <em>live</em></span>
        </div>
      </>
    ),
  },
  {
    label: "Deep",
    icon: <DeepIcon />,
    when: "When the answer is spread across many documents.",
    ask: "Summarise where the payments migration stands.",
    trace: "Read 9 documents across Notion, Linear and Slack",
    body: (
      <>
        <div className="mode-groups">
          <div><span className="is-done">Done</span>New provider live for EU cards</div>
          <div><span className="is-doing">In progress</span>US cards, 3 open issues</div>
          <div><span className="is-blocked">Blocked</span>Refunds wait on a contract change</div>
        </div>
        <div className="mode-chips">
          <span><BrandGlyph name="notion" size={12} /> Migration plan</span>
          <span><BrandGlyph name="linear" size={12} /> PAY-12</span>
          <span><BrandGlyph name="slack" size={12} /> #payments</span>
          <span>+6 more</span>
        </div>
      </>
    ),
  },
  {
    label: "Chart",
    icon: <ChartIcon />,
    when: "When the answer is a number, by person, team or week.",
    ask: "Chart commits by author this quarter.",
    trace: "Counted 154 commits from GitHub",
    body: (
      <>
        <p className="mode-chart-title">Commits by author &middot; this quarter</p>
        <div className="mode-bars">
          {BARS.map(([name, n]) => (
            <div key={name} className="mode-bar">
              <span>{name}</span>
              <i style={{ ["--w" as string]: `${(n / BARS[0][1]) * 100}%` }} />
              <b>{n}</b>
            </div>
          ))}
        </div>
      </>
    ),
  },
  {
    label: "Agent",
    icon: <ActIcon />,
    when: "When you want something done: an issue, a message, a page.",
    ask: "Create a Linear issue for the scheduler bug.",
    trace: "Drafted with your Linear account · waiting for you",
    body: (
      <div className="mode-draft">
        <div className="mode-draft-head">
          <span><BrandGlyph name="linear" size={14} /> Linear &middot; Platform team</span>
          <em>Draft &middot; not sent</em>
        </div>
        <strong>Scheduler skips jobs after a deploy</strong>
        <p>Jobs stay pending after a worker restarts. Add a retry check before the next release.</p>
        <div className="mode-draft-foot">
          <span>Nothing is created until you approve.</span>
          <span className="mode-draft-btns"><i>Edit</i><b>Approve</b></span>
        </div>
      </div>
    ),
  },
];

/**
 * Section 3: the four modes as a list a reader can scan (what each is for),
 * beside the result that mode returns. Steps through on its own so a reader
 * who never clicks still sees all four; stops for good once they choose one,
 * pauses while the pointer is over it, and never moves for reduced motion.
 */
export function ModesFigure() {
  const [active, setActive] = useState(0);
  const [auto, setAuto] = useState(true);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) setAuto(false);
  }, []);

  useEffect(() => {
    if (!auto || paused) return;
    const id = window.setTimeout(() => setActive((i) => (i + 1) % MODES.length), STEP_MS);
    return () => window.clearTimeout(id);
  }, [active, auto, paused]);

  const mode = MODES[active];

  return (
    <div
      className="modes2"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div className="modes2-list" role="tablist" aria-label="Modes" aria-orientation="vertical">
        {MODES.map((m, i) => (
          <button
            key={m.label}
            type="button"
            role="tab"
            id={`mode-tab-${i}`}
            aria-selected={i === active}
            aria-controls="mode-panel"
            className={i === active ? "is-on" : ""}
            onClick={() => {
              setActive(i);
              setAuto(false);
            }}
          >
            <span className="modes2-icon">{m.icon}</span>
            <span className="modes2-text">
              <b>{m.label}</b>
              <span>{m.when}</span>
            </span>
            {i === active && auto && (
              <i
                key={active}
                className="modes2-progress"
                style={{ animationDuration: `${STEP_MS}ms`, animationPlayState: paused ? "paused" : "running" }}
              />
            )}
          </button>
        ))}
      </div>

      <div className="site-frame modes2-stage">
        <p className="modes2-when"><b>{mode.label}:</b> {mode.when}</p>
        <div
          className="modes-window"
          id="mode-panel"
          role="tabpanel"
          aria-labelledby={`mode-tab-${active}`}
        >
          <div className="modes-bar">
            <span /><span /><span />
            <b>Handbook</b>
          </div>
          <div className="modes-body" key={active}>
            <p className="duo-q">{mode.ask}</p>
            <div className="modes-reply">
              <span className="hero-mark">HB</span>
              <div>
                <p className="mode-trace">&#10003; {mode.trace}</p>
                {mode.body}
              </div>
            </div>
            <div className="modes-composer">
              <span className="modes2-icon">{mode.icon}</span>
              <i>Ask a follow-up&hellip;</i>
              <em>{mode.label}</em>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
