"use client";

/**
 * What one section of a chart is made of, shown while the cursor is on it.
 *
 * The rows used to sit in a list under the chart, which made the card a table
 * with a picture on top -- a chart's job is to be read at a glance. On hover
 * the question is always "what is THIS bar", so the answer is the section's
 * name, its value and share, and the individual rows behind it.
 *
 * Positioned by the caller (it knows the cursor) and `pointer-events: none`,
 * so the tip can never sit between the cursor and the section it describes --
 * which would un-hover it and make the chart flicker.
 */

import { useEffect, useRef, useState } from "react";

export type TipRow = {
  subject?: string | null;
  actor?: string | null;
  state?: string | null;
  at?: string | null;
};

const MAX_ROWS = 4;

function when(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export default function ChartTip({
  title,
  value,
  share,
  rows,
  hide,
  x,
  y,
}: {
  title: string;
  value: string;
  share?: number;
  rows: TipRow[];
  /** The field the chart is grouped by: it is already the title, so repeating
   *  it on every row is noise. */
  hide?: string | null;
  x: number;
  y: number;
}) {
  const shown = rows.slice(0, MAX_ROWS);
  const more = rows.length - shown.length;

  // Flip to the cursor's left near the right edge. Measured off our own
  // offsetParent rather than taking a width prop, because the four chart
  // shapes know their width in four different ways (a measured plot, a pie's
  // diameter, a bare list) and none of them should have to care. It matters
  // most on a phone, where the tip is nearly as wide as the card.
  const ref = useRef<HTMLDivElement>(null);
  const [flip, setFlip] = useState(false);
  useEffect(() => {
    const el = ref.current;
    const parent = el?.offsetParent as HTMLElement | null;
    if (!el || !parent) return;
    setFlip(x + el.offsetWidth + 16 > parent.clientWidth);
  }, [x, y, title]);

  return (
    <div
      ref={ref}
      className={`chart-tip${flip ? " is-flipped" : ""}`}
      style={{ left: x, top: y }}
      role="tooltip"
      aria-hidden
    >
      <p className="chart-tip-title">{title}</p>
      <p className="chart-tip-value">
        {value}
        {share != null && <span className="chart-tip-share">{Math.round(share)}%</span>}
      </p>
      {shown.length > 0 && (
        <ul className="chart-tip-rows">
          {shown.map((row, i) => {
            const parts = [
              hide !== "subject" ? row.subject : null,
              hide !== "actor" ? row.actor : null,
              hide !== "state" ? row.state : null,
            ].filter(Boolean) as string[];
            return (
              <li key={i}>
                {/* Omitted when absent, never "Unknown": a placeholder in a
                    provenance line invites reading it as a fact. */}
                <span className="chart-tip-row-main">{parts.join(" · ") || "—"}</span>
                {when(row.at) && <span className="chart-tip-row-when">{when(row.at)}</span>}
              </li>
            );
          })}
          {more > 0 && <li className="chart-tip-more">+{more} more</li>}
        </ul>
      )}
    </div>
  );
}
