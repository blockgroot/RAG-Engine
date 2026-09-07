"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

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

import ChartTip from "./ChartTip";
import {
  CATEGORY_COLORS,
  SERIES_COLORS,
  cappedCategories,
  categoryColors,
  pick,
} from "./chartColors";

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
  const short = date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
  // A week bucket and a day bucket both start on a date, so "Jul 6" meant
  // either "that Monday" or "that day" depending on a period the axis does
  // not show. The prefix is what distinguishes them.
  return period === "week" ? `w/c ${short}` : short;
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

/**
 * Axis ticks that are whole numbers and never repeat.
 *
 * Fixed fractions of the max produced a DUPLICATED label on small counts: a
 * max of 3 with quarter steps gives 0.75/1.5/2.25/3, which rounds to
 * 1, 2, 2, 3 -- an axis reading "3 2 2 1 0". These are counts of real things,
 * so a fractional tick is meaningless anyway; the step is an integer and the
 * result is deduplicated.
 */
const NICE_STEPS = [1, 2, 5, 10, 20, 25, 50, 100, 250, 500, 1000];

function axisTicks(max: number, wanted = 4): number[] {
  if (max <= 0) return [0];
  const step =
    NICE_STEPS.find((s) => max / s <= wanted) ??
    Math.ceil(max / wanted);
  const ticks: number[] = [];
  for (let v = 0; v <= max; v += step) ticks.push(v);
  const last = ticks[ticks.length - 1];
  // The top of the scale, but only when it is not crowding the tick below
  // it -- "9 10" side by side is two labels for one reading.
  if (last !== max && max - last >= step / 2) ticks.push(max);
  return ticks;
}

export type DetailRow = {
  subject?: string | null;
  actor?: string | null;
  state?: string | null;
  at?: string | null;
  url?: string | null;
};

/**
 * The rows belonging to one section of a chart.
 *
 * They live in the HOVER, not in a list under the chart: a chart's job is to
 * be read at a glance, and repeating its contents underneath makes the card
 * a table with a picture on top. On hover the question is always "what is
 * THIS bar", so the rows are filtered to that bar.
 *
 * Matched on the field the chart is grouped BY -- actor, state or subject --
 * and by time bucket when it is not grouped at all. Unmatched means no rows
 * rather than all rows: showing a repository's commits under a different
 * repository's slice would be worse than showing none.
 */
function detailsFor(
  rows: DetailRow[],
  { groupBy, group, bucket, period }: {
    groupBy?: string | null;
    group?: string | null;
    bucket?: string | null;
    period: string;
  },
): DetailRow[] {
  if (!rows.length) return [];
  if (group != null && groupBy) {
    const key = group.trim().toLowerCase();
    const field = (row: DetailRow) =>
      groupBy === "actor" ? row.actor
      : groupBy === "state" ? row.state
      : groupBy === "subject" ? row.subject
      : null;
    return rows.filter((row) => (field(row) || "").trim().toLowerCase() === key);
  }
  if (bucket) {
    const want = bucketKey(bucket, period);
    return rows.filter((row) => row.at && bucketKey(row.at, period) === want);
  }
  return rows;
}

/** The bucket a timestamp falls in, as the server's `date_trunc` would. */
function bucketKey(iso: string, period: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const y = date.getUTCFullYear();
  const m = date.getUTCMonth();
  if (period === "quarter") return `${y}-Q${Math.floor(m / 3)}`;
  if (period === "month") return `${y}-${m}`;
  if (period === "week") {
    // Monday-based, matching Postgres `date_trunc('week', …)`.
    const monday = new Date(Date.UTC(y, m, date.getUTCDate()));
    const shift = (monday.getUTCDay() + 6) % 7;
    monday.setUTCDate(monday.getUTCDate() - shift);
    return monday.toISOString().slice(0, 10);
  }
  return date.toISOString().slice(0, 10);
}

export function Chart({
  chart,
  points,
  period,
  unit,
  groupBy,
  details = [],
}: {
  chart: string;
  points: Point[];
  period: string;
  unit?: string;
  groupBy?: string | null;
  /** The rows this chart counted, shown on hover for the hovered section. */
  details?: DetailRow[];
}) {
  const { buckets, series, at } = useMemo(() => pivot(points), [points]);

  // A grouped bar chart is a leaderboard, not a time series: collapse the
  // buckets and rank the series. Without this, "top editors" renders one bar
  // per person per week, which nobody can read.
  // `groupBy` from the spec wins over "did any point have a name": a pie of
  // files by person with every editor NULL used to look like one unnamed
  // filled circle, because series[0] === "".
  const grouped = Boolean(groupBy) || series.some((s) => s !== "");
  //: Beyond this a vertical bar chart's category labels collide and it
  //: becomes unreadable, which is the point at which every production chart
  //: library switches to horizontal ranked bars.
  const VERTICAL_BAR_LIMIT = 8;
  const leaderboard = chart === "bar" && grouped;
  // Above every early return: hooks must run in the same order on every
  // render, and the pie and leaderboard branches return before the plot.
  const [ref, measured] = useMeasuredWidth();
  // Which bucket the cursor is nearest. A chart you can only read by
  // squinting at gridlines is not navigable -- and `<title>` tooltips need a
  // hit on a 4px dot, which on a dense chart is most of the way to unusable.
  const [near, setNear] = useState<number | null>(null);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  // Gradient ids must be unique per chart: several charts share one page, and
  // a duplicate id makes every later chart reuse the first one's fill.
  const gid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const ranked = useMemo(() => {
    if (!grouped) return [];
    // Capped to the palette: eleven categories over six colours meant two
    // slices the same colour and a legend that could not be read.
    return cappedCategories(
      series
        .map((name) => ({
          name: name.trim() || "Unknown",
          value: buckets.reduce((sum, b) => sum + at(b, name), 0),
        }))
        .sort((a, b) => b.value - a.value),
    );
  }, [grouped, series, buckets, at]);

  // Built from every name this chart will draw, so collisions are resolved
  // once and the same category keeps its colour in the plot, the legend and
  // the readout.
  const palette = useMemo(
    () =>
      categoryColors([
        ...series,
        ...ranked.map((r) => r.name),
        ...(series.length === 1 ? buckets : []),
      ]),
    [series, ranked, buckets],
  );

  if (points.length === 0) return null;

  if (chart === "diverging_bar") return <DivergingBar points={points} />;

  // A requested pie STAYS a pie: asking for one and being handed a number is
  // not an answer to the question asked. With a single group there is nothing
  // to take shares of, so the slices become the time buckets instead -- the
  // same rows, split by when they happened, which is what someone asking
  // "pie of commits" wants to see.
  if (chart === "pie" && ranked.length === 1 && buckets.length > 1) {
    return (
      <Pie
        rows={buckets.map((b) => ({
          name: formatBucket(b, period),
          value: at(b, series[0] ?? ""),
          // The bucket itself, so the tip can find the rows for this slice.
          bucket: b,
        }))}
        unit={unit}
        details={details}
        period={period}
      />
    );
  }

  // Only when even the finest bucket leaves ONE value: a lone bar has nothing
  // to compare against and a one-slice pie is a circle labelled 100%.
  if ((leaderboard || chart === "pie") && ranked.length === 1 && buckets.length <= 1) {
    const only = ranked[0];
    const name = series.find((s) => (s.trim() || "Unknown") === only.name) ?? "";
    return (
      <Stat
        label={only.name}
        value={only.value}
        unit={unit}
        buckets={buckets}
        period={period}
        seriesName={name}
        at={at}
      />
    );
  }

  if (chart === "pie") {
    const rows = ranked.length
      ? ranked
      : buckets.map((b) => ({
          name: formatBucket(b, period),
          value: at(b, series[0] ?? ""),
        }));
    return (
      <Pie
        rows={rows}
        unit={unit}
        details={details}
        groupBy={ranked.length ? groupBy : null}
        period={period}
      />
    );
  }

  // Someone who asks for a bar chart means BARS: an axis, a baseline, and
  // columns standing on it. A list of thin tracks reads as coloured lines --
  // it is the right shape for twenty categories and the wrong one for four.
  if (leaderboard && ranked.length <= VERTICAL_BAR_LIMIT) {
    return (
      <CategoryBars
        rows={ranked}
        unit={unit}
        palette={palette}
        measured={measured}
        containerRef={ref}
        details={details}
        groupBy={groupBy}
        period={period}
      />
    );
  }

  if (leaderboard) {
    const max = Math.max(...ranked.map((r) => r.value), 1);
    const total = ranked.reduce((sum, r) => sum + r.value, 0);
    return (
      <ul
        className="chart-rank"
        onPointerMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          setCursor({ x: event.clientX - box.left, y: event.clientY - box.top });
        }}
        onPointerLeave={(event) => {
          // A touch fires `pointerleave` the moment the finger lifts, so
          // honouring it would make a tap show the tip and hide it in the same
          // gesture -- the metadata would be unreachable on a phone. On touch
          // the tip stays until the next tap moves it, which is what "tap to
          // inspect" means.
          if (event.pointerType === "touch") return;
          setCursor(null);
          setNear(null);
        }}
      >
        {ranked.map((row, i) => (
          <li
            key={row.name}
            className="chart-rank-row"
            onPointerEnter={() => setNear(i)}
          >
            <span className="chart-rank-label" title={row.name}>
              {/* The position, because a ranking read top-to-bottom still
                  makes you count rows to answer "who is third?" */}
              <span className="chart-rank-index">{i + 1}</span>
              {row.name}
            </span>
            <span className="chart-rank-track">
              <span
                className="chart-rank-fill"
                style={{
                  width: `${(row.value / max) * 100}%`,
                  background: pick(palette, row.name, i),
                }}
              />
            </span>
            <span className="chart-rank-value">
              {withUnit(row.value, unit)}
              {/* The share, because a bar whose only reference is the longest
                  bar tells you rank but not weight -- "top editor" over 4% of
                  the activity is a different fact from over 60%. */}
              {ranked.length > 1 && total > 0 && (
                <span className="chart-rank-share">
                  {Math.round((row.value / total) * 100)}%
                </span>
              )}
            </span>
          </li>
        ))}
        {near != null && ranked[near] && cursor && (
          <ChartTip
            title={ranked[near].name}
            value={withUnit(ranked[near].value, unit)}
            share={(ranked[near].value / Math.max(1, total)) * 100}
            rows={detailsFor(details, {
              group: ranked[near].name,
              groupBy,
              period,
            })}
            hide={groupBy}
            x={cursor.x}
            y={cursor.y}
          />
        )}
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

  const gridlines = axisTicks(max, 2).map((value) => ({ value, y: y(value) }));

  return (
    <div className="chart-scroll" ref={ref}>
      <svg
        className="chart-svg"
        viewBox={`0 0 ${width} ${HEIGHT}`}
        width={width}
        height={HEIGHT}
        role="img"
        aria-label={`${chart} chart, ${buckets.length} buckets`}
        onPointerLeave={(event) => {
          if (event.pointerType === "touch") return;  // see CategoryBars
          setNear(null);
          setCursor(null);
        }}
        onPointerMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          if (!box.width) return;
          setCursor({
            x: event.clientX - box.left,
            y: event.clientY - box.top,
          });
          // Client pixels -> viewBox units, so the hit test stays correct
          // while the SVG is scaled to the card.
          const vx = ((event.clientX - box.left) / box.width) * width;
          const slot = plotW / Math.max(1, buckets.length);
          const index =
            chart === "line"
              ? Math.round(((vx - PAD.left) / plotW) * (buckets.length - 1))
              : Math.floor((vx - PAD.left) / slot);
          setNear(Math.max(0, Math.min(buckets.length - 1, index)));
        }}
      >
        <defs>
          {series.map((name, si) => (
            <linearGradient
              key={name || "all"}
              id={`${gid}-${si}`}
              x1="0"
              y1="0"
              x2="0"
              y2="1"
            >
              <stop offset="0%" stopColor={pick(palette, name, si)} stopOpacity={0.28} />
              <stop offset="100%" stopColor={pick(palette, name, si)} stopOpacity={0.02} />
            </linearGradient>
          ))}
        </defs>

        {near != null && (
          <line
            x1={chart === "line" ? x(near) : PAD.left + (plotW / buckets.length) * (near + 0.5)}
            x2={chart === "line" ? x(near) : PAD.left + (plotW / buckets.length) * (near + 0.5)}
            y1={PAD.top}
            y2={PAD.top + plotH}
            className="chart-guide"
          />
        )}

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
              const color = pick(palette, name, si);
              return (
                <g key={name || "all"}>
                  {/* A filled area under a single series. Three points and a
                      thin stroke read as a fragment of a chart; the fill
                      gives the same numbers a shape. Only for one series,
                      because overlapping fills hide each other. */}
                  {series.length === 1 && buckets.length > 1 && (
                    <path
                      d={`${path} L ${x(buckets.length - 1)} ${PAD.top + plotH} L ${x(0)} ${PAD.top + plotH} Z`}
                      fill={`url(#${gid}-${si})`}
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
                        // The cursor's point and the LATEST point are larger:
                        // "where are we now" is the question a trend line is
                        // usually asked, and it was the same 3px dot as every
                        // other reading.
                        r={near === i ? 6 : i === buckets.length - 1 ? 5 : 4}
                        fill={near === i ? color : "var(--surface)"}
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
                    // A single-series bar chart coloured one colour is a row
                    // of identical grey-green sticks; the bucket is the only
                    // thing distinguishing them, so the bucket picks the
                    // colour. A real multi-series chart must keep colour
                    // meaning the SERIES, or the legend stops being true.
                    const fill =
                      series.length === 1
                        ? pick(palette, b, i)
                        : pick(palette, name, si);
                    return (
                      <rect
                        key={name || "all"}
                        className="chart-bar"
                        x={cx}
                        y={cursor}
                        width={barW}
                        height={h}
                        fill={fill}
                        rx={Math.min(4, barW / 3)}
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

      {near != null && buckets[near] && cursor && (
        <ChartTip
          title={formatBucket(buckets[near], period)}
          value={series
            .map((name) =>
              `${name ? `${name}: ` : ""}${withUnit(at(buckets[near], name), unit)}`,
            )
            .join(" · ")}
          rows={detailsFor(details, {
            bucket: buckets[near],
            period,
            groupBy: null,
          })}
          x={cursor.x}
          y={cursor.y}
        />
      )}

      {chart === "line" && series.length > 1 && (
        <ul className="chart-legend">
          {series.map((name, si) => (
            <li key={name || "all"}>
              <span
                className="chart-swatch"
                style={{ background: pick(palette, name, si) }}
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

function CategoryBars({
  rows,
  unit,
  palette,
  measured,
  containerRef,
  details,
  groupBy,
  period,
}: {
  rows: { name: string; value: number }[];
  unit?: string;
  palette: Map<string, string>;
  measured: number;
  containerRef: React.Ref<HTMLDivElement>;
  details: DetailRow[];
  groupBy?: string | null;
  period: string;
}) {
  /**
   * A real bar chart: baseline, value axis, and columns standing on it.
   *
   * The ranked-track list this replaces is correct for twenty categories and
   * wrong for four -- at four it reads as a few coloured lines floating in a
   * card, which is not what someone asking for "a bar chart" pictured. Above
   * `VERTICAL_BAR_LIMIT` the caller keeps the ranked list, because vertical
   * category labels collide long before the bars run out of room.
   */
  const [near, setNear] = useState<number | null>(null);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const height = 260;
  const pad = { top: 24, right: 12, bottom: 46, left: 44 };
  const width = Math.max(measured, 320);
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const max = niceMax(Math.max(...rows.map((r) => r.value), 0));
  const slot = plotW / Math.max(1, rows.length);
  // Capped so three categories are columns rather than slabs, and floored so
  // twenty are still visible.
  const barW = Math.max(10, Math.min(64, slot * 0.62));
  const y = (v: number) => pad.top + plotH - (v / max) * plotH;
  const gridlines = axisTicks(max, 4).map((value) => ({ value, y: y(value) }));

  return (
    <div className="chart-scroll" ref={containerRef}>
      <svg
        className="chart-svg"
        viewBox={`0 0 ${width} ${height}`}
        width={width}
        height={height}
        role="img"
        aria-label={`bar chart, ${rows.length} categories`}
        onPointerLeave={(event) => {
          if (event.pointerType === "touch") return;  // see CategoryBars
          setNear(null);
          setCursor(null);
        }}
        onPointerMove={(event) => {
          const box = event.currentTarget.getBoundingClientRect();
          setCursor({ x: event.clientX - box.left, y: event.clientY - box.top });
        }}
      >
        {gridlines.map((line) => (
          <g key={line.y}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={line.y}
              y2={line.y}
              className="chart-grid"
            />
            <text x={pad.left - 8} y={line.y + 4} textAnchor="end" className="chart-axis">
              {Math.round(line.value).toLocaleString()}
            </text>
          </g>
        ))}

        {/* The baseline, drawn heavier than the gridlines: a bar chart without
            a visible axis to stand on is a set of floating rectangles. */}
        <line
          x1={pad.left}
          x2={width - pad.right}
          y1={pad.top + plotH}
          y2={pad.top + plotH}
          className="chart-axis-line"
        />

        {rows.map((row, i) => {
          const h = Math.max(2, (row.value / max) * plotH);
          const x = pad.left + slot * i + (slot - barW) / 2;
          const color = pick(palette, row.name, i);
          const label =
            row.name.length > 14 ? `${row.name.slice(0, 13)}…` : row.name;
          return (
            <g
              key={row.name}
              onPointerEnter={() => setNear(i)}
              className="chart-bar-group"
            >
              {/* A full-height hit area, so hovering does not require landing
                  on a short bar. */}
              <rect
                x={pad.left + slot * i}
                y={pad.top}
                width={slot}
                height={plotH}
                fill="transparent"
              />
              <rect
                className="chart-bar"
                x={x}
                y={y(row.value)}
                width={barW}
                height={h}
                rx={Math.min(5, barW / 4)}
                fill={color}
                opacity={near == null || near === i ? 1 : 0.45}
              >
                <title>{`${row.name}: ${withUnit(row.value, unit)}`}</title>
              </rect>
              <text
                x={x + barW / 2}
                y={y(row.value) - 8}
                textAnchor="middle"
                className="chart-value"
              >
                {row.value.toLocaleString()}
              </text>
              <text
                x={pad.left + slot * i + slot / 2}
                y={height - 26}
                textAnchor="middle"
                className="chart-axis"
              >
                {label}
              </text>
            </g>
          );
        })}
      </svg>

      {/* A standing summary, so the card says something before the cursor
          arrives. The per-section detail is the hover's job. */}
      <p className="chart-readout" role="status">
        <span className="chart-readout-item">
          {rows.length} {rows.length === 1 ? "category" : "categories"} ·{" "}
          {withUnit(rows.reduce((sum, r) => sum + r.value, 0), unit)} total
        </span>
      </p>

      {near != null && rows[near] && cursor && (
        <ChartTip
          title={rows[near].name}
          value={withUnit(rows[near].value, unit)}
          share={
            (rows[near].value /
              Math.max(1, rows.reduce((sum, r) => sum + r.value, 0))) * 100
          }
          rows={detailsFor(details, {
            group: rows[near].name,
            groupBy,
            period,
          })}
          hide={groupBy}
          x={cursor.x}
          y={cursor.y}
        />
      )}
    </div>
  );
}


function Stat({
  label,
  value,
  unit,
  buckets,
  period,
  seriesName,
  at,
}: {
  label: string;
  value: number;
  unit?: string;
  buckets: string[];
  period: string;
  seriesName: string;
  at: (bucket: string, series: string) => number;
}) {
  /**
   * One group, rendered as the number it is.
   *
   * Reached when a ranking or a share resolves to a single group -- a real
   * situation on a small team, a new connector or a filtered chart, and the
   * one case where the ordinary shapes actively mislead: a lone bar has
   * nothing to compare against and a one-slice pie is a circle labelled
   * 100%. The trend is drawn beside it because "4 commits" and "4 commits,
   * all in one week" are different facts.
   */
  const values = buckets.map((b) => at(b, seriesName));
  const peak = Math.max(...values, 1);
  const W = 220;
  const H = 44;
  const step = buckets.length > 1 ? W / (buckets.length - 1) : 0;
  const path = values
    .map((v, i) => `${i === 0 ? "M" : "L"} ${i * step} ${H - (v / peak) * (H - 6) - 3}`)
    .join(" ");

  return (
    <div className="chart-stat">
      <div>
        <p className="chart-stat-value">{withUnit(value, unit)}</p>
        <p className="chart-stat-label">{label}</p>
      </div>
      {buckets.length > 1 && (
        <svg
          className="chart-stat-spark"
          viewBox={`0 0 ${W} ${H}`}
          width={W}
          height={H}
          role="img"
          aria-label={`trend over ${buckets.length} ${period}s`}
        >
          <path
            d={`${path} L ${W} ${H} L 0 ${H} Z`}
            fill="var(--chart-1)"
            opacity={0.12}
            stroke="none"
          />
          <path d={path} fill="none" stroke="var(--chart-1)" strokeWidth={2} />
        </svg>
      )}
      {buckets.length > 1 && (
        <p className="chart-stat-range">
          {formatBucket(buckets[0], period)} – {formatBucket(buckets[buckets.length - 1], period)}
        </p>
      )}
    </div>
  );
}


function Pie({
  rows,
  unit,
  details = [],
  groupBy,
  period = "month",
}: {
  rows: { name: string; value: number; bucket?: string }[];
  unit?: string;
  details?: DetailRow[];
  groupBy?: string | null;
  period?: string;
}) {
  const [hovered, setHovered] = useState<number | null>(null);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [ref, measured] = useMeasuredWidth(320);
  const palette = useMemo(
    () => categoryColors(rows.map((r) => r.name)),
    [rows],
  );
  const total = rows.reduce((sum, row) => sum + row.value, 0);
  if (total <= 0) return null;
  // Scaled to the card. A 220px circle in a 1100px panel is not a small
  // chart, it is a chart that looks unfinished.
  const size = Math.max(240, Math.min(measured, 360));
  const cx = size / 2;
  const cy = size / 2;
  const r = size * 0.38;
  //: A donut, not a disc. The hole carries the TOTAL, which a pie has to put
  //: in a caption or leave out -- and every slice then reads as a share of a
  //: number you can see rather than of an unstated whole. It also stops a
  //: single dominant slice from becoming an undifferentiated filled circle.
  const inner = r * 0.58;
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
    const labelR = (r + inner) / 2;
    return {
      ...row,
      i,
      pct,
      color: pick(palette, row.name, i),
      labelX: cx + labelR * Math.cos(mid),
      labelY: cy + labelR * Math.sin(mid),
      // Inside the slice or not at all. At 1.22r a small slice's label floated
      // OUTSIDE the circle with no leader line, which read as a stray number
      // above the chart rather than as a label. The legend already names every
      // slice with its exact count.
      showOnSlice: pct >= 9,
      d: full
        ? [
            `M ${cx} ${cy - r} A ${r} ${r} 0 1 1 ${cx} ${cy + r}`,
            `A ${r} ${r} 0 1 1 ${cx} ${cy - r} Z`,
            `M ${cx} ${cy - inner} A ${inner} ${inner} 0 1 0 ${cx} ${cy + inner}`,
            `A ${inner} ${inner} 0 1 0 ${cx} ${cy - inner} Z`,
          ].join(" ")
        : [
            `M ${x1} ${y1}`,
            `A ${r} ${r} 0 ${large} 1 ${x2} ${y2}`,
            `L ${cx + inner * Math.cos(angle)} ${cy + inner * Math.sin(angle)}`,
            `A ${inner} ${inner} 0 ${large} 0 ${cx + inner * Math.cos(start)} ${cy + inner * Math.sin(start)}`,
            "Z",
          ].join(" "),
    };
  });
  const active = hovered != null ? slices[hovered] : null;
  return (
    <div
      className="chart-pie"
      ref={ref}
      onPointerMove={(event) => {
        const box = event.currentTarget.getBoundingClientRect();
        setCursor({ x: event.clientX - box.left, y: event.clientY - box.top });
      }}
      onPointerLeave={(event) => {
        if (event.pointerType === "touch") return;  // see CategoryBars
        setCursor(null);
      }}
    >
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
            fillRule="evenodd"
            className="chart-pie-slice"
            // Separated by the card's own background rather than a hardcoded
            // white, so the gaps stay gaps in either theme.
            stroke="var(--surface)"
            // Thicker separator on the hovered slice: emphasis that changes
            // no geometry, so it cannot move the slice under the cursor.
            strokeWidth={hovered === slice.i ? 3 : 2}
            opacity={hovered == null || hovered === slice.i ? 1 : 0.32}
            onPointerEnter={() => setHovered(slice.i)}
            onPointerLeave={(event) => {
              if (event.pointerType === "touch") return;
              setHovered(null);
            }}
          >
            <title>
              {`${slice.name}: ${withUnit(slice.value, unit)} (${Math.round(slice.pct)}%)`}
            </title>
          </path>
        ))}
        {/* The whole, in the hole. Hovering swaps it for that slice, so the
            centre always answers "of what?" */}
        <text
          x={cx}
          y={cy - 4}
          textAnchor="middle"
          className="chart-donut-total"
          pointerEvents="none"
        >
          {(active ? active.value : total).toLocaleString()}
        </text>
        <text
          x={cx}
          y={cy + 14}
          textAnchor="middle"
          className="chart-donut-caption"
          pointerEvents="none"
        >
          {active ? `${Math.round(active.pct)}%` : unit || "total"}
        </text>

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
      {active && cursor && (
        <ChartTip
          title={active.name}
          value={withUnit(active.value, unit)}
          share={active.pct}
          rows={detailsFor(details, {
            group: groupBy ? active.name : null,
            groupBy,
            bucket: active.bucket ?? null,
            period,
          })}
          hide={groupBy}
          x={cursor.x}
          y={cursor.y}
        />
      )}
      <ul className="chart-legend">
        {slices.map((slice) => (
          <li
            key={slice.name}
            data-active={hovered === slice.i || undefined}
            onPointerEnter={() => setHovered(slice.i)}
            onPointerLeave={(event) => {
              if (event.pointerType === "touch") return;
              setHovered(null);
            }}
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
