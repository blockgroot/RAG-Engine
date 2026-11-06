"use client";

import { useEffect, useState, type ReactNode } from "react";
import { BrandGlyph } from "@/components/BrandGlyph";
import { ActIcon, AskIcon, ChartIcon, DeepIcon } from "@/components/ModeIcons";
import { HbLogo } from "@/components/site/HbLogo";

const STEP_MS = 6000;

const MODES: Array<{
  label: string;
  icon: ReactNode;
  when: string;
  ask: string;
  /** A finished step, as the chat shows while it works. Agent has none: its reply speaks for itself. */
  trace?: string;
  body: ReactNode;
}> = [
  {
    label: "Ask",
    icon: <AskIcon />,
    when: "For a fact or a quick answer.",
    ask: "Who reviewed the checkout change?",
    trace: "Read 1 pull request live from GitHub",
    // Mirrors a real GitHub answer: pill with the live chip, cited prose,
    // and with one source, the single line under the answer.
    body: (
      <>
        <span className="mode-pill">
          <i />GitHub<em>GitHub agent</em><em>GitHub · live</em>
        </span>
        <p className="mode-text">
          Leah reviewed and approved it on Tuesday. Sam asked for a retry on
          failed card payments, which was added before the merge.
        </p>
        <div className="mode-sources">
          <p>Source</p>
          <ol>
            <li><span>checkout#218 · Retry failed card payments</span><em><BrandGlyph name="github" size={11} /> GitHub</em></li>
          </ol>
        </div>
      </>
    ),
  },
  {
    label: "Deep",
    icon: <DeepIcon />,
    when: "When the answer is spread across many documents.",
    ask: "Summarise where the payments migration stands.",
    trace: "Read the top 9 documents in full",
    // Mirrors the real Deep answer (ChatMessage + AnswerText): provenance pill,
    // prose with gray superscript citations, then the Sources list.
    body: (
      <>
        <span className="mode-pill">
          <i />Notion + Linear + Slack<em>Connected answer</em>
        </span>
        <p className="mode-text">
          The migration is about two-thirds done. EU cards moved to the new
          provider on 14 October<sup>1</sup>; US cards follow once three open
          issues close<sup>2</sup>.
        </p>
        <ul className="mode-list">
          <li>Refunds still run on the old provider until legal signs the contract change<sup>3</sup>.</li>
        </ul>
        <div className="mode-sources">
          <p>Sources</p>
          <ol>
            <li><span>Payments migration plan</span><em><BrandGlyph name="notion" size={11} /> Notion</em></li>
            <li><span>PAY-12 · US card cut-over</span><em><BrandGlyph name="linear" size={11} /> Linear</em></li>
            <li><span>#payments thread from Priya</span><em><BrandGlyph name="slack" size={11} /> Slack</em></li>
          </ol>
        </div>
      </>
    ),
  },
  {
    label: "Chart",
    icon: <ChartIcon />,
    when: "When the answer is a number, by person, team or week.",
    ask: "Show product output by month as a stacked bar from the Annual Manufacturing Report.",
    trace: "Read 12 figures from a Google Drive report",
    body: (
      // A real Chart-mode screenshot (docs/poc-images/chart-from-document.png, cropped).
      // eslint-disable-next-line @next/next/no-img-element
      <img
        className="mode-shot"
        src="/site/chart-mode.png"
        width={1190}
        height={545}
        alt="Stacked bar chart of production by month, split by product, read from a Google Drive report"
        loading="lazy"
      />
    ),
  },
  {
    label: "Agent",
    icon: <ActIcon />,
    when: "When you want something done: an issue, a message, a page.",
    ask: "Create a Linear issue for the scheduler bug.",
    // Mirrors the real reply (actions/service.py) and ActionCard: label, "as
    // you, in …", Where/details/Title/Description, four buttons and the timer.
    body: (
      <>
        <p className="mode-text">Here&rsquo;s what I&rsquo;ll do. Check it, then approve.</p>
        <div className="mode-draft">
          <div className="mode-draft-head">
            <strong>Linear: Save issue</strong>
            <span>as you, in Linear</span>
          </div>
          <dl>
            <dt>Where</dt><dd>Platform</dd>
            <dt>Title</dt><dd><b>Scheduler skips jobs after a deploy</b></dd>
            <dt>Description</dt><dd>Jobs stay pending after a worker restarts. Add a retry check before the next release.</dd>
          </dl>
          <div className="mode-draft-btns">
            <b>Approve</b><i>Edit</i><i>Ask for changes</i><i>Cancel</i>
            <span>Expires in 14:50</span>
          </div>
        </div>
      </>
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
          {/* Every mode is rendered in the same grid cell and only the active
              one is visible, so the window is always as tall as the tallest
              answer and does not jump when the tab changes. */}
          <div className="modes-stack">
            {MODES.map((m, i) => (
              <div
                key={m.label}
                className={i === active ? "modes-body is-on" : "modes-body"}
                aria-hidden={i !== active}
              >
                <p className="duo-q">{m.ask}</p>
                <div className="modes-reply">
                  <HbLogo />
                  <div>
                    {m.trace && <p className="mode-trace">&#10003; {m.trace}</p>}
                    {m.body}
                  </div>
                </div>
                <div className="modes-composer">
                  <span className="modes2-icon">{m.icon}</span>
                  <i>Ask a follow-up&hellip;</i>
                  <em>{m.label}</em>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
