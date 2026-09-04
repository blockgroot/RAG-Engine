"use client";

import { useEffect, useMemo, useRef, useState } from "react";

/**
 * Line, bar, pie and stacked-bar charts as plain SVG.
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

const PAD = { top: 16, right: 16, bottom: 30, left: 44 };
const HEIGHT = 240;

/** Widest a chart grows to. Past this a 4-point line is a lot of white space
 *  with a stripe across it, and the eye has to travel to compare two bars. */
const MAX_PLOT_WIDTH = 720;

/**
 * The rendered width of the card this chart is in.
 *
 * Without it the SVG was a FIXED 320px inside a card that is often three
 * times that, so a chart with few buckets sat in the left third of an empty
 * frame and read as a rendering bug. The measurement also keeps the crowded
 * case working: a wide chart still overflows into `.chart-scroll` rather than
 * squashing thirty buckets into a smear.
 */
function useMeasuredWidth(fallback = 560) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);

  useEffect(() => {
    const node = ref.current;
    // No ResizeObserver on the server, and none in older browsers: the
    // fallback width renders a correct chart, just not a fitted one.
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const measured = Math.round(entries[0]?.contentRect.width ?? 0);
      if (measured > 0) setWidth(measured);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return [ref, Math.min(width, MAX_PLOT_WIDTH)] as const;
}

/** "1 issue", not "1 issues" -- a legend row saying "1 issues" looks like the
 *  number is being generated rather than read. */
function withUnit(value: number, unit?: string): string {
    const shown = value.toLocaleString();
  if (!unit) return shown;
  const singular = value === 1 && unit.endsWith("s") ? unit.slice(0, -1) : unit;
  return `${shown} ${singular}`;
}
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
  groupBy,
}: {
  chart: string;
  points: Point[];
  period: string;
  unit?: string;
  groupBy?: string | null;
}) {
  const { buckets, series, at } = useMemo(() => pivot(points), [points]);

  // A grouped bar chart is a leaderboard, not a time series: collapse the
  // buckets and rank the series. Without this, "top editors" renders one bar
  // per person per week, which nobody can read.
  // `groupBy` from the spec wins over "did any point have a name": a pie of
  // files by person with every editor NULL used to look like one unnamed
  // filled circle, because series[0] === "".
  const grouped = Boolean(groupBy) || series.some((s) => s !== "");
  const leaderboard = chart === "bar" && grouped;
  // Above every early return: hooks must run in the same order on every
  // render, and the pie and leaderboard branches return before the plot.
  const [ref, measured] = useMeasuredWidth();
  const ranked = useMemo(() => {
    if (!grouped) return [];
    return series
      .map((name) => ({
        name: name.trim() || "Unknown",
        value: buckets.reduce((sum, b) => sum + at(b, name), 0),
      }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 12);
  }, [grouped, series, buckets, at]);

  if (points.length === 0) return null;

  if (chart === "diverging_bar") return <DivergingBar points={points} />;

  if (chart === "pie") {
    const rows = ranked.length
      ? ranked
      : buckets.map((b) => ({
          name: formatBucket(b, period),
          value: at(b, series[0] ?? ""),
        }));
    return <Pie rows={rows} unit={unit} />;
  }

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
              {withUnit(row.value, unit)}
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
    measured,
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
    <div className="chart-scroll" ref={ref}>
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
              const color = SERIES_COLORS[si % SERIES_COLORS.length];
              return (
                <g key={name || "all"}>
                  {/* A filled area under a single series. Three points and a
                      thin stroke read as a fragment of a chart; the fill
                      gives the same numbers a shape. Only for one series,
                      because overlapping fills hide each other. */}
                  {series.length === 1 && buckets.length > 1 && (
                    <path
                      d={`${path} L ${x(buckets.length - 1)} ${PAD.top + plotH} L ${x(0)} ${PAD.top + plotH} Z`}
                      fill={color}
                      opacity={0.1}
                      stroke="none"
                    />
                  )}
                  <path
                    d={path}
                    fill="none"
                    stroke={color}
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  {buckets.map((b, i) => (
                    <g key={b}>
                      <circle
                        cx={x(i)}
                        cy={y(at(b, name))}
                        r={4}
                        fill="var(--surface)"
                        stroke={color}
                        strokeWidth={2}
                      >
                        <title>
                          {`${formatBucket(b, period)}: ${withUnit(at(b, name), unit)}`}
                        </title>
                      </circle>
                      {/* The value itself, while the points are far enough
                          apart to read. A chart of four numbers should not
                          need a hover to tell you the four numbers. */}
                      {series.length === 1 && buckets.length <= 8 && (
                        <text
                          x={x(i)}
                          y={y(at(b, name)) - 12}
                          textAnchor="middle"
                          className="chart-value"
                          pointerEvents="none"
                        >
                          {at(b, name).toLocaleString()}
                        </text>
                      )}
                    </g>
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
                          {`${formatBucket(b, period)}${name ? ` - ${name}` : ""}: ${withUnit(value, unit)}`}
                        </title>
                      </rect>
                    );
                  })}
                  {series.length === 1 && buckets.length <= 12 && at(b, series[0]) > 0 && (
                    <text
                      x={cx + barW / 2}
                      y={y(at(b, series[0])) - 6}
                      textAnchor="middle"
                      className="chart-value"
                      pointerEvents="none"
                    >
                      {at(b, series[0]).toLocaleString()}
                    </text>
                  )}
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

function Pie({
  rows,
  unit,
}: {
  rows: { name: string; value: number }[];
  unit?: string;
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  const [ref, measured] = useMeasuredWidth(320);
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  if (total <= 0) return null;
  // Scaled to the card. A 220px circle in a 1100px panel is not a small
  // chart, it is a chart that looks unfinished.
  const size = Math.max(240, Math.min(measured, 360));
  const cx = size / 2;
  const cy = size / 2;
  const r = size * 0.36;
  let angle = -Math.PI / 2;
  const slices = rows.map((row, i) => {
    const sweep = (row.value / total) * Math.PI * 2;
    const start = angle;
    const mid = start + sweep / 2;
    angle += sweep;
    const x1 = cx + r * Math.cos(start);
    const y1 = cy + r * Math.sin(start);
    const x2 = cx + r * Math.cos(angle);
    const y2 = cy + r * Math.sin(angle);
    const large = sweep > Math.PI ? 1 : 0;
    const full = sweep >= Math.PI * 2 - 1e-6;
    const pct = (row.value / total) * 100;
    const labelR = r * 0.62;
    return {
      ...row,
      i,
      pct,
      color: SERIES_COLORS[i % SERIES_COLORS.length],
      labelX: cx + labelR * Math.cos(mid),
      labelY: cy + labelR * Math.sin(mid),
      // Inside the slice or not at all. At 1.22r a small slice's label floated
      // OUTSIDE the circle with no leader line, which read as a stray number
      // above the chart rather than as a label. The legend already names every
      // slice with its exact count.
      showOnSlice: pct >= 12,
      d: full
        ? `M ${cx} ${cy - r} A ${r} ${r} 0 1 1 ${cx} ${cy + r} A ${r} ${r} 0 1 1 ${cx} ${cy - r} Z`
        : `M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z`,
    };
  });
  const active = hovered != null ? slices[hovered] : null;
  return (
    <div className="chart-pie" ref={ref}>
      <svg
        className="chart-svg"
        viewBox={`0 0 ${size} ${size}`}
        width={size}
        height={size}
        role="img"
        aria-label="pie chart"
      >
        {slices.map((slice) => (
          <path
            key={slice.name}
            d={slice.d}
            fill={slice.color}
            className="chart-pie-slice"
            opacity={hovered == null || hovered === slice.i ? 1 : 0.45}
            onMouseEnter={() => setHovered(slice.i)}
            onMouseLeave={() => setHovered(null)}
          >
            <title>
              {`${slice.name}: ${withUnit(slice.value, unit)} (${Math.round(slice.pct)}%)`}
            </title>
          </path>
        ))}
        {slices.map((slice) =>
          slice.showOnSlice ? (
            <text
              key={`label-${slice.name}`}
              x={slice.labelX}
              y={slice.labelY}
              textAnchor="middle"
              dominantBaseline="middle"
              className="chart-pie-label"
              pointerEvents="none"
            >
              {`${Math.round(slice.pct)}%`}
            </text>
          ) : null,
        )}
      </svg>
      {active && (
        <p className="chart-pie-hover" role="status">
          {active.name}: {withUnit(active.value, unit)} ({Math.round(active.pct)}%)
        </p>
      )}
      <ul className="chart-legend">
        {slices.map((slice) => (
          <li
            key={slice.name}
            data-active={hovered === slice.i || undefined}
            onMouseEnter={() => setHovered(slice.i)}
            onMouseLeave={() => setHovered(null)}
          >
            <span className="chart-swatch" style={{ background: slice.color }} />
            <span className="chart-legend-name">{slice.name}</span>
            <span className="chart-legend-value">
              {withUnit(slice.value, unit)} ({Math.round(slice.pct)}%)
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
