"use client";

import { useMemo } from "react";

/**
 * Line, bar and stacked-bar charts as plain SVG.
 *
 * No chart library on purpose. This app has no UI kit and no Tailwind - plain
 * CSS variables and global classes - so a charting framework would become the
 * single heaviest dependency in the frontend, for three shapes that are a few
 * dozen lines of arithmetic each. A 325MB dependency already cost this project
 * a deploy once.
 *
 * Every number rendered here was computed by SQL over `activity_facts`. This
 * component does no aggregation of its own beyond stacking and ranking, so
 * there is no second place a total can be wrong.
 */

export type Point = {
  bucket: string;
  group: string | null;
  /** A second dimension, only where a chart genuinely needs two: a
   *  diverging bar is topic (the row) BY sentiment label (the
   *  segment), which one grouping cannot express. */
  series?: string | null;
  value: number;
};

/** Distinct enough to tell series apart, muted enough to sit in the page. */
const SERIES_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
];

const PAD = { top: 12, right: 12, bottom: 28, left: 40 };
const HEIGHT = 200;
/** Bars get their own width so a 30-bucket chart scrolls instead of squashing. */
const MIN_BAR_SLOT = 28;

function formatBucket(iso: string, period: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  if (period === "quarter") {
    return `Q${Math.floor(date.getUTCMonth() / 3) + 1} ${date.getUTCFullYear()}`;
  }
  if (period === "month") {
    return date.toLocaleDateString(undefined, {
      month: "short",
      year: "2-digit",
    });
  }
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Bucket then series then value, preserving the order the server sent. */
function pivot(points: Point[]) {
  const buckets: string[] = [];
  const series: string[] = [];
  const cells = new Map<string, number>();

  for (const point of points) {
    if (!buckets.includes(point.bucket)) buckets.push(point.bucket);
    const name = point.group ?? "";
    if (!series.includes(name)) series.push(name);
    const key = `${point.bucket} ${name}`;
    cells.set(key, (cells.get(key) ?? 0) + point.value);
  }
  return {
    buckets,
    series,
    at: (b: string, s: string) => cells.get(`${b} ${s}`) ?? 0,
  };
}

/** A y-axis that ends on a round number, so the top gridline is readable. */
function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / magnitude) * magnitude;
}

export function Chart({
  chart,
  points,
  period,
  unit,
}: {
  chart: string;
  points: Point[];
  period: string;
  unit?: string;
}) {
  const { buckets, series, at } = useMemo(() => pivot(points), [points]);

  // A grouped bar chart is a leaderboard, not a time series: collapse the
  // buckets and rank the series. Without this, "top editors" renders one bar
  // per person per week, which nobody can read.
  const leaderboard = chart === "bar" && series.length > 0 && series[0] !== "";
  const ranked = useMemo(() => {
    if (!leaderboard) return [];
    return series
      .map((name) => ({
        name,
        value: buckets.reduce((sum, b) => sum + at(b, name), 0),
      }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 12);
  }, [leaderboard, series, buckets, at]);

  if (points.length === 0) return null;

  if (chart === "diverging_bar") return <DivergingBar points={points} />;

  if (leaderboard) {
    const max = Math.max(...ranked.map((r) => r.value), 1);
    return (
      <ul className="chart-rank">
        {ranked.map((row, i) => (
          <li key={row.name} className="chart-rank-row">
            <span className="chart-rank-label" title={row.name}>
              {row.name}
            </span>
            <span className="chart-rank-track">
              <span
                className="chart-rank-fill"
                style={{
                  width: `${(row.value / max) * 100}%`,
                  background: SERIES_COLORS[i % SERIES_COLORS.length],
                }}
              />
            </span>
            <span className="chart-rank-value">
              {row.value.toLocaleString()}
            </span>
          </li>
        ))}
      </ul>
    );
  }

  const stacked = chart === "stacked_bar";
  const totals = buckets.map((b) =>
    stacked
      ? series.reduce((sum, s) => sum + at(b, s), 0)
      : Math.max(...series.map((s) => at(b, s))),
  );
  const max = niceMax(Math.max(...totals, 0));

  const width = Math.max(
    320,
    PAD.left + PAD.right + buckets.length * MIN_BAR_SLOT,
  );
  const plotW = width - PAD.left - PAD.right;
  const plotH = HEIGHT - PAD.top - PAD.bottom;
  const y = (value: number) => PAD.top + plotH - (value / max) * plotH;
  // A single bucket has no span, so dividing by (n - 1) would be Infinity.
  // One point is a dot, not a crash.
  const x = (i: number) =>
    buckets.length === 1
      ? PAD.left + plotW / 2
      : PAD.left + (i / (buckets.length - 1)) * plotW;

  const gridlines = [0, 0.5, 1].map((f) => ({ value: max * f, y: y(max * f) }));

  return (
    <div className="chart-scroll">
      <svg
        className="chart-svg"
        viewBox={`0 0 ${width} ${HEIGHT}`}
        width={width}
        height={HEIGHT}
        role="img"
        aria-label={`${chart} chart, ${buckets.length} buckets`}
      >
        {gridlines.map((line) => (
          <g key={line.y}>
            <line
              x1={PAD.left}
              x2={width - PAD.right}
              y1={line.y}
              y2={line.y}
              className="chart-grid"
            />
            <text x={4} y={line.y + 4} className="chart-axis">
              {Math.round(line.value).toLocaleString()}
            </text>
          </g>
        ))}

        {chart === "line"
          ? series.map((name, si) => {
              const path = buckets
                .map(
                  (b, i) => `${i === 0 ? "M" : "L"} ${x(i)} ${y(at(b, name))}`,
                )
                .join(" ");
              return (
                <g key={name || "all"}>
                  <path
                    d={path}
                    fill="none"
                    stroke={SERIES_COLORS[si % SERIES_COLORS.length]}
                    strokeWidth={2}
                  />
                  {buckets.map((b, i) => (
                    <circle
                      key={b}
                      cx={x(i)}
                      cy={y(at(b, name))}
                      r={3}
                      fill={SERIES_COLORS[si % SERIES_COLORS.length]}
                    >
                      <title>
                        {`${formatBucket(b, period)}: ${at(b, name).toLocaleString()}${
                          unit ? ` ${unit}` : ""
                        }`}
                      </title>
                    </circle>
                  ))}
                </g>
              );
            })
          : buckets.map((b, i) => {
              const slot = plotW / buckets.length;
              const barW = Math.max(6, slot * 0.6);
              const cx = PAD.left + slot * i + slot / 2 - barW / 2;
              let cursor = PAD.top + plotH;
              return (
                <g key={b}>
                  {series.map((name, si) => {
                    const value = at(b, name);
                    if (value <= 0) return null;
                    const h = (value / max) * plotH;
                    cursor -= h;
                    return (
                      <rect
                        key={name || "all"}
                        x={cx}
                        y={cursor}
                        width={barW}
                        height={h}
                        fill={SERIES_COLORS[si % SERIES_COLORS.length]}
                        rx={2}
                      >
                        <title>
                          {`${formatBucket(b, period)}${name ? ` - ${name}` : ""}: ${value.toLocaleString()}${
                            unit ? ` ${unit}` : ""
                          }`}
                        </title>
                      </rect>
                    );
                  })}
                </g>
              );
            })}

        {buckets.map((b, i) => {
          // Thin the labels out rather than overlapping them.
          const step = Math.ceil(buckets.length / 8);
          if (i % step !== 0) return null;
          const slot = plotW / buckets.length;
          const cx = chart === "line" ? x(i) : PAD.left + slot * i + slot / 2;
          return (
            <text
              key={b}
              x={cx}
              y={HEIGHT - 8}
              textAnchor="middle"
              className="chart-axis"
            >
              {formatBucket(b, period)}
            </text>
          );
        })}
      </svg>

      {chart === "line" && series.length > 1 && (
        <ul className="chart-legend">
          {series.map((name, si) => (
            <li key={name || "all"}>
              <span
                className="chart-swatch"
                style={{ background: SERIES_COLORS[si % SERIES_COLORS.length] }}
              />
              {name || "All"}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Sentiment order, negative to positive. Fixed here so the segments always
 *  stack in the same direction regardless of what order the rows arrive in. */
const SENTIMENT_ORDER = [
  "very negative",
  "negative",
  "neutral",
  "positive",
  "very positive",
];

const SENTIMENT_COLORS: Record<string, string> = {
  "very negative": "#b42318",
  negative: "#f04438",
  neutral: "#98a2b3",
  positive: "#45b26b",
  "very positive": "#0f766e",
};

/**
 * Diverging stacked bar: neutral centred, negative left, positive right.
 *
 * The standard shape for Likert data, and the only one where every topic
 * shares a baseline - which is what lets someone read the lean across twenty
 * questions at a glance instead of comparing middle segments that start at
 * different places. Half of neutral goes each side, which is what centres it.
 *
 * Rows are sorted by favourability, so "which topics went worst" is the top or
 * bottom of the list rather than something to hunt for.
 */
function DivergingBar({ points }: { points: Point[] }) {
  const topics = new Map<string, Map<string, number>>();
  for (const point of points) {
    const topic = point.group ?? "Overall";
    const label = (point.series ?? "neutral").toLowerCase();
    const row = topics.get(topic) ?? new Map<string, number>();
    row.set(label, (row.get(label) ?? 0) + point.value);
    topics.set(topic, row);
  }

  const rows = [...topics.entries()].map(([topic, counts]) => {
    const total = [...counts.values()].reduce((a, b) => a + b, 0) || 1;
    const share = (label: string) => ((counts.get(label) ?? 0) / total) * 100;
    const neutralHalf = share("neutral") / 2;
    // Everything left of centre, in stacking order outward.
    const left = share("very negative") + share("negative") + neutralHalf;
    const favourable = share("positive") + share("very positive");
    return { topic, counts, total, share, left, favourable };
  });
  rows.sort((a, b) => b.favourable - a.favourable);

  return (
    <div className="chart-diverge">
      {rows.map((row) => (
        <div key={row.topic} className="chart-diverge-row">
          <span className="chart-diverge-label" title={row.topic}>
            {row.topic}
          </span>
          <span className="chart-diverge-track">
            {/* Offset so each row's neutral midpoint lands on the same axis. */}
            <span
              className="chart-diverge-bar"
              style={{ marginLeft: `${50 - row.left}%` }}
            >
              {SENTIMENT_ORDER.map((label) => {
                const width = row.share(label);
                if (width <= 0) return null;
                return (
                  <span
                    key={label}
                    className="chart-diverge-seg"
                    style={{
                      width: `${width}%`,
                      background: SENTIMENT_COLORS[label],
                    }}
                    title={`${row.topic} - ${label}: ${Math.round(
                      row.counts.get(label) ?? 0,
                    )} of ${row.total}`}
                  />
                );
              })}
            </span>
          </span>
          <span className="chart-diverge-value">
            {Math.round(row.favourable)}%
          </span>
        </div>
      ))}
      <ul className="chart-legend">
        {SENTIMENT_ORDER.map((label) => (
          <li key={label}>
            <span
              className="chart-swatch"
              style={{ background: SENTIMENT_COLORS[label] }}
            />
            {label}
          </li>
        ))}
      </ul>
      <p className="chart-note">
        Share of responses. Percentage shown is positive or very positive.
      </p>
    </div>
  );
}
