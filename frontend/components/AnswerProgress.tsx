"use client";

import { useEffect, useState } from "react";
import { BrandGlyph, type BrandName } from "./BrandGlyph";

const BRAND: Record<string, BrandName> = {
  Notion: "notion",
  "Google Drive": "drive",
  Slack: "slack",
  Linear: "linear",
  GitHub: "github",
};

/**
 * One progress line, split for display. The server sends plain sentences
 * (app/rag/progress.py); "Found in Linear: “A” and “B”" is shown as the app's
 * logo and one chip per document, so a list of titles reads as a list.
 */
function parse(step: string): { app?: BrandName; text: string; docs: string[] } {
  const found = /^Found in ([^:]+): (.*)$/.exec(step);
  if (found) {
    // Slack titles keep their *bold* markers; a chip shows the words only.
    const plain = (t: string) => t.replace(/\*([^*]+)\*/g, "$1");
    const docs = [...found[2].matchAll(/“([^”]+)”(?: from ([^“,]+?))?(?=,| and |$)/g)].map((m) =>
      plain(m[2] ? `${m[1]} · ${m[2].trim()}` : m[1]),
    );
    const more = /(\d+) more$/.exec(found[2]);
    if (more) docs.push(`+${more[1]} more`);
    return { app: BRAND[found[1]], text: `Found in ${found[1]}`, docs };
  }
  const searching = /^Searching (.+)$/.exec(step);
  const app = searching ? BRAND[searching[1]] : undefined;
  return { app, text: step, docs: [] };
}

function Step({ step, live }: { step: string; live?: boolean }) {
  const { app, text, docs } = parse(step);
  return (
    <li className="ap-step" data-live={live || undefined}>
      <span className="ap-mark" aria-hidden>
        {live ? (
          <span className="ap-pulse" />
        ) : (
          <svg viewBox="0 0 16 16" fill="none"><path d="m4 8.5 2.5 2.5L12 5.5" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" /></svg>
        )}
      </span>
      <div className="ap-body">
        <span className="ap-text">
          {app && <BrandGlyph name={app} size={14} />}
          {text}
        </span>
        {docs.length > 0 && (
          <span className="ap-docs">
            {docs.map((d) => (
              <span key={d} className="ap-doc" title={d}>{d}</span>
            ))}
          </span>
        )}
      </div>
    </li>
  );
}

/** What the answer is doing right now, as a short timeline with a timer. */
export function AnswerProgress({ steps }: { steps?: string[] }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const t = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(t);
  }, []);
  const all = steps?.length ? steps : ["Finding a grounded answer"];
  const done = all.slice(0, -1).slice(-5);
  const live = all.at(-1)!;
  return (
    <div className="ap" role="status" aria-live="polite">
      <div className="ap-head">
        <span className="ap-title">Working on your answer</span>
        <span className="ap-time">{seconds}s</span>
      </div>
      <ol className="ap-steps">
        {done.map((s, i) => (
          <Step key={`${i}-${s}`} step={s} />
        ))}
        <Step key={`live-${live}`} step={live} live />
      </ol>
    </div>
  );
}
