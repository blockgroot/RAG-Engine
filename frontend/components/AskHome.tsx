"use client";

/**
 * The Ask page's empty state and mode switch: a greeting, and the four modes
 * as one switch above the box; Chart's starters drawn as their chart, and
 * Agent's three steps with requests to start from.
 */

import { useEffect, useState, type ReactNode } from "react";
import { BrandGlyph, type BrandName } from "@/components/BrandGlyph";
import { ActIcon, AskIcon, ChartIcon, DeepIcon } from "@/components/ModeIcons";

export type AskMode = "ask" | "deep" | "chart" | "act";

/** The connector key a suggestion came from (server's `question_sources`) → its logo. */
export const SOURCE_GLYPH: Record<string, BrandName> = {
  notion: "notion",
  google: "drive",
  slack: "slack",
  linear: "linear",
  github: "github",
  forms: "forms",
};

/** "claude-haiku-5-5" → "Haiku 5.5"; anything else is shown as it came. */
export function modelName(label: string): string {
  const m = /^claude-([a-z]+)-(\d+)-(\d+)$/i.exec(label.trim());
  if (!m) return label;
  return `${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}.${m[3]}`;
}

function greeting(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** The chart a starter names in its own first words ("Pie chart of…"). Decoration only. */
type ChartKind = "bar" | "hbar" | "stack" | "line" | "area" | "pie";
export function chartKind(q: string): ChartKind {
  const s = q.toLowerCase();
  if (s.startsWith("horizontal")) return "hbar";
  if (s.startsWith("stacked")) return "stack";
  if (s.startsWith("line")) return "line";
  if (s.startsWith("area")) return "area";
  if (s.startsWith("pie") || s.startsWith("donut")) return "pie";
  return "bar";
}

/** A tiny drawing of that chart, so the six starters do not look the same. */
export function ChartThumb({ kind }: { kind: ChartKind }) {
  const c = "var(--accent)";
  const soft = "color-mix(in srgb, var(--accent) 35%, white)";
  return (
    <svg className={`ask-chart-thumb is-${kind}`} viewBox="0 0 64 40" aria-hidden>
      {kind === "bar" && [14, 24, 18, 32, 26].map((h, i) => <rect key={i} x={6 + i * 11} y={38 - h} width="7" height={h} rx="1.5" fill={i === 3 ? c : soft} />)}
      {kind === "hbar" && [44, 30, 52, 22].map((w, i) => <rect key={i} x="6" y={5 + i * 8.5} width={w} height="5.5" rx="1.5" fill={i === 2 ? c : soft} />)}
      {kind === "stack" && [[12, 8], [16, 10], [10, 14], [18, 12], [14, 9]].map(([a, b], i) => (
        <g key={i}><rect x={6 + i * 11} y={38 - a} width="7" height={a} rx="1" fill={c} /><rect x={6 + i * 11} y={38 - a - b} width="7" height={b} rx="1" fill={soft} /></g>
      ))}
      {kind === "line" && <polyline points="4,30 16,24 28,27 40,14 52,18 60,8" fill="none" stroke={c} strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />}
      {kind === "area" && <path d="M4 38 L4 28 L16 22 L28 25 L40 12 L52 16 L60 6 L60 38 Z" fill={soft} stroke={c} strokeWidth="2" strokeLinejoin="round" />}
      {kind === "pie" && (
        <g transform="translate(32 20)">
          <circle r="16" fill={soft} />
          <path d="M0 0 L0 -16 A16 16 0 0 1 15.2 5 Z" fill={c} />
          <circle r="6" fill="#fff" />
        </g>
      )}
    </svg>
  );
}

/** Agent mode's empty state: what happens, in three steps, and requests to start from. */
export function AgentIntro({
  examples,
  onPick,
}: {
  examples: Array<{ glyph: BrandName; text: string }>;
  onPick: (text: string) => void;
}) {
  const steps: Array<[string, string]> = [
    ["You ask", "In your own words"],
    ["Handbook drafts it", "A card with every field filled in"],
    ["You approve", "Then it is sent, as you"],
  ];
  return (
    <div className="ask-agent">
      <ol className="ask-agent-flow">
        {steps.map(([t, d], i) => (
          <li key={t} style={{ ["--i" as string]: i }}>
            <span className="ask-agent-n">{i + 1}</span>
            <b>{t}</b>
            <small>{d}</small>
          </li>
        ))}
      </ol>
      {examples.length > 0 && (
        <div className="ask-agent-try">
          <span>Try one</span>
          {examples.map((e, i) => (
            <button key={e.text} type="button" style={{ ["--i" as string]: i }} onClick={() => onPick(e.text)}>
              <BrandGlyph name={e.glyph} size={14} />
              {e.text}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function AskGreeting({
  name,
  title,
  copy,
}: {
  name: string | null;
  /** In another mode, its own title instead of the greeting. */
  title: string | null;
  copy: ReactNode;
}) {
  // The hour is read in the browser after mount, so the server's render and
  // the first client render agree.
  const [hello, setHello] = useState("Hello");
  useEffect(() => setHello(greeting(new Date().getHours())), []);

  return (
    <div className="ask-hello">
      <h1 key={title ?? "hello"}>
        {title ?? (
          <>
            {hello}
            {name ? <>, <span>{name}</span></> : null}
          </>
        )}
      </h1>
      <p className="ask-hello-copy">{copy}</p>
    </div>
  );
}

const MODES: Array<{ key: AskMode; label: string; icon: ReactNode; hint: string }> = [
  { key: "ask", label: "Ask", icon: <AskIcon />, hint: "A cited answer from your documents" },
  { key: "deep", label: "Deep", icon: <DeepIcon />, hint: "Reads whole documents. Extra effort reads more of them." },
  { key: "chart", label: "Chart", icon: <ChartIcon />, hint: "A chart from your tools and tables" },
  { key: "act", label: "Agent", icon: <ActIcon />, hint: "Drafts an action you approve" },
];

export function ModeSwitch({
  mode,
  onPick,
  agent,
  disabled,
}: {
  mode: AskMode;
  onPick: (m: AskMode) => void;
  agent: boolean;
  disabled: boolean;
}) {
  const shown = MODES.filter((m) => m.key !== "act" || agent);
  const at = Math.max(0, shown.findIndex((m) => m.key === mode));
  return (
    <div className="ask-modes" role="radiogroup" aria-label="Mode" style={{ ["--n" as string]: shown.length, ["--at" as string]: at }}>
      <i className="ask-modes-thumb" aria-hidden />
      {shown.map((m) => (
        <button
          key={m.key}
          type="button"
          role="radio"
          aria-checked={mode === m.key}
          title={m.hint}
          disabled={disabled}
          onClick={() => onPick(m.key)}
        >
          {m.icon}
          {m.label}
        </button>
      ))}
    </div>
  );
}

/**
 * Types example questions into the placeholder, one after another, while the
 * box is empty. Off for reduced motion (the plain placeholder is kept).
 */
export function useTypedPlaceholder(examples: string[], on: boolean, fallback: string): string {
  const [text, setText] = useState(fallback);
  const key = examples.join("\n");

  useEffect(() => {
    if (!on || examples.length === 0 || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setText(fallback);
      return;
    }
    let q = 0;
    let c = 0;
    let timer: ReturnType<typeof setTimeout>;
    const step = () => {
      const target = `Try: ${examples[q]}`;
      if (c < target.length) {
        c += 1;
        setText(target.slice(0, c));
        timer = setTimeout(step, 38);
      } else {
        timer = setTimeout(() => {
          q = (q + 1) % examples.length;
          c = 0;
          step();
        }, 2600);
      }
    };
    timer = setTimeout(step, 900);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, on, fallback]);

  return text;
}
