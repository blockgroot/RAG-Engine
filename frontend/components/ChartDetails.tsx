"use client";

import { useState } from "react";

/**
 * The rows a chart counted.
 *
 * A bar labelled "4" answers "how many" and nothing else. Which commits, by
 * whom, when, and where to read them are the questions that follow
 * immediately -- and every one of those columns is already on the row being
 * counted, so withholding them makes the chart less trustworthy for no gain.
 *
 * Deliberately a LIST, not a second chart: the count is the answer and this is
 * its evidence. Collapsed past a handful of rows so it annotates the chart
 * rather than burying it.
 */

const VISIBLE = 5;

export type DetailRow = {
  subject?: string | null;
  actor?: string | null;
  state?: string | null;
  at?: string | null;
  url?: string | null;
};

function formatWhen(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function ChartDetails({
  rows,
  label,
}: {
  rows: DetailRow[];
  label: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? rows : rows.slice(0, VISIBLE);
  const hidden = rows.length - shown.length;

  return (
    <div className="chart-details">
      <p className="chart-details-head">
        Behind this chart · newest {label}
        {rows.length === 1 ? "" : "s"} first
      </p>
      <ul className="chart-details-list">
        {shown.map((row, i) => {
          const name = (row.subject || "").trim() || label;
          const when = formatWhen(row.at);
          return (
            <li key={`${name}-${row.at}-${i}`} className="chart-details-row">
              <span className="chart-details-name">
                {row.url ? (
                  // Opens the actual page, commit or issue. The url was
                  // already on the counted row.
                  <a href={row.url} target="_blank" rel="noreferrer">
                    {name}
                  </a>
                ) : (
                  name
                )}
              </span>
              {/* Every field is omitted when absent rather than rendered as
                  "Unknown" -- a placeholder in a provenance line invites
                  someone to read it as a fact. */}
              {row.actor && <span className="chart-details-actor">{row.actor}</span>}
              {row.state && <span className="chart-details-state">{row.state}</span>}
              {when && <span className="chart-details-when">{when}</span>}
            </li>
          );
        })}
      </ul>
      {hidden > 0 && (
        <button
          type="button"
          className="chart-details-more"
          onClick={() => setExpanded(true)}
        >
          Show {hidden} more
        </button>
      )}
    </div>
  );
}
