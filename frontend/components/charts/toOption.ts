/**
 * Chart props + theme -> an ECharts option. Pure: no React, no DOM.
 *
 * Every number drawn was computed by SQL on the server. This module only
 * pivots, orders and ranks -- it never aggregates beyond summing a bucket's
 * duplicate cells -- so there is no second place a total can be wrong.
 *
 * Pure so `components/__checks__/toOption.check.ts` can run the REAL
 * implementation under `node --experimental-strip-types`.
 */
import type { EChartsOption } from "echarts";
import { SERIES_COLORS, cappedCategories, categoryColors, pick } from "../chartColors";

export type Point = {
  bucket: string;
  group: string | null;
  /** A second dimension, only where a chart genuinely needs two: a
   *  diverging bar is topic (the row) BY sentiment label (the segment). */
  series?: string | null;
  value: number;
};

export type DetailRow = {
  subject?: string | null;
  actor?: string | null;
  state?: string | null;
  at?: string | null;
  url?: string | null;
  /** Recorded fields (label, priority, ...) for matching a hover to a bar. */
  attrs?: Record<string, unknown>;
};

export type ChartProps = {
  chart: string;
  points: Point[];
  period: string;
  unit?: string;
  /** Unit per series name, so a combo chart can scale each measure on its own. */
  seriesUnits?: Record<string, string>;
  /** What an empty value means per field, from the source: a Linear issue
   *  with no assignee is "Unassigned", not "Unknown". */
  blankLabels?: Record<string, string>;
  groupBy?: string | null;
  /** A second grouping: each category is "group · split". */
  splitBy?: string | null;
  /** The rows this chart counted, shown in the tooltip for the hovered section. */
  details?: DetailRow[];
};

export type ChartTheme = {
  text: string;
  muted: string;
  grid: string;
  border: string;
  background: string;
  fontFamily?: string;
  /** Resolved values for the `var(--chart-n)` palette entries. ECharts parses
   *  colours itself, so a CSS variable string never reaches it. */
  colors: Record<string, string>;
  reducedMotion: boolean;
};

/** The light values in globals.css, for when no stylesheet is there (checks). */
export const DEFAULT_THEME: ChartTheme = {
  text: "#101828",
  muted: "#667085",
  grid: "#e4e7ec",
  border: "#d0d5dd",
  background: "#f7f9fb",
  colors: {
    "var(--chart-1)": "#0f766e",
    "var(--chart-2)": "#2563eb",
    "var(--chart-3)": "#b45309",
    "var(--chart-4)": "#7c3aed",
    "var(--chart-5)": "#be123c",
    "var(--chart-6)": "#0e7490",
  },
  reducedMotion: false,
};

/** A number with its unit, the way a person writes it: "₹130 lakh", "12%",
 *  "1 issue" (not "1 issues"). A unit that starts with a symbol is written
 *  against the number -- decided by the character, not a list of currencies.
 *  Mirrors `format_value` in app/insights/describe.py. */
const UNIT_SYMBOL = /^[^\p{L}\p{N}\s(]+/u;
export function withUnit(value: number, unit?: string): string {
  const shown = value.toLocaleString();
  const u = (unit || "").trim();
  if (!u) return shown;
  if (u === "%") return `${shown}%`;
  const symbol = u.match(UNIT_SYMBOL);
  if (symbol) {
    const rest = u.slice(symbol[0].length).trim();
    return `${symbol[0]}${shown}${rest ? ` ${rest}` : ""}`;
  }
  const singular = value === 1 && u.endsWith("s") ? u.slice(0, -1) : u;
  return `${shown} ${singular}`;
}

/** Buckets are UTC midnights from `date_trunc`; formatting them in local time
 *  turned "2026-04-01" into "Mar 31" west of Greenwich. */
export function formatBucket(iso: string, period: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  if (period === "quarter") {
    return `Q${Math.floor(date.getUTCMonth() / 3) + 1} ${date.getUTCFullYear()}`;
  }
  if (period === "month") {
    return date.toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });
  }
  const short = date.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
  // A week bucket and a day bucket both start on a date; the prefix is what
  // distinguishes "that Monday's week" from "that day".
  return period === "week" ? `w/c ${short}` : short;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;

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
  return { buckets, series, at: (b: string, s: string) => cells.get(`${b} ${s}`) ?? 0 };
}

/** Joins a two-way split ("Sana · Chain-Guard") into one category; `detailsFor`
 *  splits it back to match hover rows on BOTH fields. */
const SPLIT_SEP = " · ";

function fieldOf(row: DetailRow, dim: string, wanted?: string) {
  if (dim === "actor") return row.actor;
  if (dim === "state") return row.state;
  if (dim === "subject") return row.subject;
  const value = row.attrs?.[dim];
  if (Array.isArray(value)) {
    const hit = value.find((v) => String(v).trim().toLowerCase() === wanted);
    return hit == null ? (value.length ? String(value[0]) : null) : String(hit);
  }
  return value == null ? null : String(value);
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
    monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
    return monday.toISOString().slice(0, 10);
  }
  return date.toISOString().slice(0, 10);
}

/**
 * The rows behind one section. Matched on the field the chart is grouped BY,
 * or by time bucket when it is not grouped. Unmatched means no rows rather
 * than all rows: another repository's commits under this bar is worse than none.
 */
export function detailsFor(
  rows: DetailRow[],
  { groupBy, group, bucket, period, blanks = [] }: {
    groupBy?: string | null;
    group?: string | null;
    bucket?: string | null;
    period: string;
    blanks?: string[];
  },
): DetailRow[] {
  if (!rows.length) return [];
  if (group != null && groupBy) {
    const dims = groupBy.split("+");
    const parts = dims.length > 1 ? group.split(SPLIT_SEP) : [group];
    if (parts.length !== dims.length) return [];
    const norm = (v: string | null | undefined) => (v || "").trim().toLowerCase();
    const wanted = parts.map((p) =>
      p.trim() === "Unknown" || blanks.includes(p.trim()) ? "" : norm(p),
    );
    return rows.filter((row) =>
      dims.every((dim, i) => norm(fieldOf(row, dim, wanted[i])) === wanted[i]),
    );
  }
  if (bucket) {
    const want = bucketKey(bucket, period);
    return rows.filter((row) => row.at && bucketKey(row.at, period) === want);
  }
  return rows;
}

/** Sentiment order, negative to positive, fixed so segments always stack the
 *  same way regardless of row order. */
export const SENTIMENT_ORDER = ["very negative", "negative", "neutral", "positive", "very positive"];
const SENTIMENT_COLORS: Record<string, string> = {
  "very negative": "#b42318",
  negative: "#f04438",
  neutral: "#98a2b3",
  positive: "#45b26b",
  "very positive": "#0f766e",
};

//: Beyond this a vertical bar chart's category labels collide, which is where
//: every production chart library switches to horizontal ranked bars.
const VERTICAL_BAR_LIMIT = 8;
const ZOOM_AFTER = 24;

type Row = { name: string; value: number; group?: string | null; bucket?: string | null };

type Model =
  | { kind: "heatmap"; xs: string[]; ys: string[]; cells: Map<string, number>; xBy: string | null; yBy: string | null }
  | { kind: "diverging"; rows: { topic: string; counts: Map<string, number>; total: number; favourable: number }[] }
  | { kind: "pie"; donut: boolean; rows: Row[]; groupBy: string | null }
  | { kind: "rank"; horizontal: boolean; rows: Row[]; groupBy: string | null }
  | {
      kind: "axis";
      buckets: string[];
      labels: string[];
      series: string[];
      at: (b: string, s: string) => number;
      shape: "line" | "bar" | "area";
      stacked: boolean;
      combo: boolean;
      timeAxis: boolean;
    };

function model(p: ChartProps): Model {
  const blankLabels = p.blankLabels ?? {};
  const blankFor = (dim?: string | null) => (dim && blankLabels[dim]) || "Unknown";
  const split = Boolean(p.groupBy && p.splitBy);
  const groupBy = split ? `${p.groupBy}+${p.splitBy}` : p.groupBy ?? null;
  const points = split
    ? p.points.map((pt) => ({
        ...pt,
        group: [pt.group, pt.series]
          .map((v, i) => (v || "").trim() || blankFor(i === 0 ? p.groupBy : p.splitBy))
          .join(SPLIT_SEP),
        series: null,
      }))
    : p.points;

  if (p.chart === "diverging_bar") {
    const topics = new Map<string, Map<string, number>>();
    for (const pt of points) {
      const topic = pt.group ?? "Overall";
      const label = (pt.series ?? "neutral").toLowerCase();
      const row = topics.get(topic) ?? new Map<string, number>();
      row.set(label, (row.get(label) ?? 0) + pt.value);
      topics.set(topic, row);
    }
    const rows = [...topics.entries()].map(([topic, counts]) => {
      const total = [...counts.values()].reduce((a, b) => a + b, 0) || 1;
      const favourable = (((counts.get("positive") ?? 0) + (counts.get("very positive") ?? 0)) / total) * 100;
      return { topic, counts, total, favourable };
    });
    // By favourability, so "which topics went worst" is an end of the list.
    rows.sort((a, b) => b.favourable - a.favourable);
    return { kind: "diverging", rows };
  }

  if (p.chart === "heatmap") return heatmap(p, blankFor);

  const { buckets, series, at } = pivot(points);
  // A time axis stays in time order. Ranking quarters by value turned them
  // into a leaderboard ("Q1 · Pro").
  const timeAxis = buckets.some((b) => ISO_DATE.test(b));
  if (timeAxis) buckets.sort((a, b) => (Date.parse(a) || 0) - (Date.parse(b) || 0));
  const grouped = Boolean(groupBy) || series.some((s) => s !== "");
  const groupTotals: Row[] = grouped
    ? series
        .map((name) => ({
          name: name.trim() || blankFor(p.groupBy),
          value: buckets.reduce((sum, b) => sum + at(b, name), 0),
          group: name.trim() || blankFor(p.groupBy),
        }))
        .sort((a, b) => b.value - a.value)
    : [];
  const bucketRows = (): Row[] =>
    buckets.map((b) => ({
      name: timeAxis ? formatBucket(b, p.period) : b,
      value: at(b, series[0] ?? ""),
      bucket: b,
    }));

  if (p.chart === "pie" || p.chart === "donut") {
    const donut = p.chart === "donut";
    // One group in one period: a one-slice pie says nothing, so a column.
    if (groupTotals.length === 1 && buckets.length <= 1) {
      return { kind: "rank", horizontal: false, rows: groupTotals, groupBy };
    }
    // One group over time: the slices become the time buckets.
    if (groupTotals.length === 1) return { kind: "pie", donut, rows: bucketRows(), groupBy: null };
    // Capped to the palette: a legend with two identical swatches cannot be read.
    const rows = groupTotals.length
      ? cappedCategories(groupTotals).map((r) => ({ ...r, group: r.name.startsWith("Other (") ? undefined : r.name }))
      : bucketRows();
    return { kind: "pie", donut, rows, groupBy: groupTotals.length ? groupBy : null };
  }

  const barLike = p.chart === "bar" || p.chart === "horizontal_bar";
  // A grouped bar chart over no time axis -- or over ONE period, which is
  // no axis at all -- is a leaderboard: collapse the buckets, rank the groups.
  if (barLike && grouped && (!timeAxis || buckets.length === 1)) {
    return {
      kind: "rank",
      horizontal: p.chart === "horizontal_bar" || groupTotals.length > VERTICAL_BAR_LIMIT,
      rows: groupTotals,
      groupBy,
    };
  }
  if (barLike && !timeAxis && (buckets.length > VERTICAL_BAR_LIMIT || p.chart === "horizontal_bar")) {
    return {
      kind: "rank",
      horizontal: true,
      rows: bucketRows().sort((a, b) => b.value - a.value),
      groupBy: null,
    };
  }

  return {
    kind: "axis",
    buckets,
    labels: buckets.map((b) => (timeAxis ? formatBucket(b, p.period) : b)),
    series,
    at,
    // One or two points do not make a line: a lone dot reads as a broken chart.
    shape:
      p.chart === "area" || p.chart === "stacked_area" ? "area"
      : p.chart === "line" && buckets.length >= 3 ? "line"
      : "bar",
    stacked: p.chart === "stacked_bar" || p.chart === "stacked_area",
    combo: p.chart === "combo" && series.length > 1,
    timeAxis,
  };
}

/** Two breakdowns as a grid. With a second breakdown the columns are the
 *  first and the rows the second (summed over time); otherwise the columns
 *  are the periods and the rows the breakdown. */
function heatmap(p: ChartProps, blankFor: (dim?: string | null) => string): Model {
  const split = Boolean(p.groupBy && p.splitBy);
  const timeAxis = !split && p.points.some((pt) => ISO_DATE.test(pt.bucket));
  const xs: string[] = [];
  const ys: string[] = [];
  const cells = new Map<string, number>();
  const ordered = timeAxis
    ? [...p.points].sort((a, b) => (Date.parse(a.bucket) || 0) - (Date.parse(b.bucket) || 0))
    : p.points;
  for (const pt of ordered) {
    const x = split ? (pt.group || "").trim() || blankFor(p.groupBy)
      : timeAxis ? formatBucket(pt.bucket, p.period) : pt.bucket;
    const y = split ? (pt.series || "").trim() || blankFor(p.splitBy)
      : (pt.group || "").trim() || blankFor(p.groupBy);
    if (!xs.includes(x)) xs.push(x);
    if (!ys.includes(y)) ys.push(y);
    cells.set(`${x}\u0000${y}`, (cells.get(`${x}\u0000${y}`) ?? 0) + pt.value);
  }
  return { kind: "heatmap", xs, ys, cells, xBy: split ? p.groupBy ?? null : null, yBy: split ? p.splitBy ?? null : p.groupBy ?? null };
}

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
/** Tooltips are HTML and every name in them came from a connected source. */
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);

function when(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

const MAX_TIP_ROWS = 4;

/** Same anatomy as the old hover card: title, value(s), share, then the rows
 *  behind this section. The grouped-by field is the title, so rows omit it. */
function tipHtml(title: string, lines: string[], rows: DetailRow[], hide?: string | null, share?: number) {
  const shown = rows.slice(0, MAX_TIP_ROWS);
  const more = rows.length - shown.length;
  const items = shown.map((row) => {
    const parts = [
      hide !== "subject" ? row.subject : null,
      hide !== "actor" ? row.actor : null,
      hide !== "state" ? row.state : null,
    ].filter(Boolean);
    const at = when(row.at);
    return `<li><span class="chart-tip-row-main">${esc(parts.join(" · ") || "—")}</span>${
      at ? `<span class="chart-tip-row-when">${esc(at)}</span>` : ""
    }</li>`;
  });
  if (more > 0) items.push(`<li class="chart-tip-more">+${more} more</li>`);
  return (
    `<p class="chart-tip-title">${esc(title)}</p>` +
    lines.map((l) => `<p class="chart-tip-value">${l}</p>`).join("") +
    (share != null ? `<p class="chart-tip-share">${Math.round(share)}% of total</p>` : "") +
    (items.length ? `<ul class="chart-tip-rows">${items.join("")}</ul>` : "")
  );
}

type TipParam = { dataIndex: number; seriesIndex?: number; marker?: unknown; value?: unknown; percent?: number };
const first = (params: unknown): TipParam => (Array.isArray(params) ? params[0] : params) as TipParam;

export function toOption(p: ChartProps, theme: ChartTheme = DEFAULT_THEME): EChartsOption {
  const m = model(p);
  const details = p.details ?? [];
  const blanks = Object.values(p.blankLabels ?? {});
  const color = (c: string) => theme.colors[c] ?? DEFAULT_THEME.colors[c] ?? c;
  const unitFor = (name: string) => p.seriesUnits?.[name] || p.unit;
  const values = p.points.map((pt) => pt.value);
  // Counts of real things: a fractional tick ("2.5 issues") is meaningless.
  const minInterval = values.every((v) => Number.isInteger(v)) ? 1 : undefined;

  const base: EChartsOption = {
    animation: !theme.reducedMotion,
    aria: { enabled: true },
    backgroundColor: "transparent",
    textStyle: { color: theme.text, fontFamily: theme.fontFamily },
    tooltip: {
      confine: true,
      backgroundColor: theme.background,
      borderColor: theme.border,
      textStyle: { color: theme.text, fontSize: 12 },
      extraCssText: "max-width:20rem;white-space:normal;box-shadow:0 6px 24px rgba(15,23,42,0.14);",
    },
  };
  const axisStyle = {
    axisLabel: { color: theme.muted, hideOverlap: true },
    axisLine: { lineStyle: { color: theme.border } },
    axisTick: { show: false },
    splitLine: { lineStyle: { color: theme.grid } },
  };
  const legend = (show: boolean) =>
    show ? { type: "scroll" as const, bottom: 0, textStyle: { color: theme.text } } : { show: false };

  if (m.kind === "heatmap") {
    const data = m.xs.flatMap((x, xi) => m.ys.map((y, yi) => [xi, yi, m.cells.get(`${x}\u0000${y}`) ?? 0]));
    const max = Math.max(1, ...data.map((d) => d[2]));
    return {
      ...base,
      grid: { left: 8, right: 16, top: 8, bottom: 56, containLabel: true },
      xAxis: { type: "category", data: m.xs, ...axisStyle, splitArea: { show: true } },
      yAxis: { type: "category", data: m.ys, ...axisStyle, axisLabel: { color: theme.muted, width: 140, overflow: "truncate" }, splitArea: { show: true } },
      visualMap: {
        min: 0, max, calculable: true, orient: "horizontal", left: "center", bottom: 0,
        textStyle: { color: theme.muted },
        inRange: { color: [theme.background, color(SERIES_COLORS[0])] },
      },
      series: [{
        type: "heatmap",
        data,
        label: { show: m.xs.length * m.ys.length <= 60, color: theme.text, formatter: (x: { value?: unknown }) => Number((x.value as number[])[2]).toLocaleString() },
        itemStyle: { borderColor: theme.background, borderWidth: 1 },
      }],
      tooltip: {
        ...base.tooltip,
        trigger: "item",
        formatter: (params: unknown) => {
          const v = (first(params).value as number[]) ?? [0, 0, 0];
          const x = m.xs[v[0]];
          const y = m.ys[v[1]];
          return tipHtml(`${x} · ${y}`, [esc(withUnit(v[2], p.unit))], []);
        },
      },
    } as EChartsOption;
  }

  if (m.kind === "diverging") {
    // Neutral is centred: half each side, so every topic shares a baseline.
    // Negatives are drawn as negative shares and stack leftward from zero.
    const share = (r: (typeof m.rows)[number], label: string) => ((r.counts.get(label) ?? 0) / r.total) * 100;
    const seg = (label: string, sign: 1 | -1, half = false) => ({
      type: "bar" as const,
      name: label,
      stack: "sentiment",
      barMaxWidth: 18,
      itemStyle: { color: SENTIMENT_COLORS[label] },
      data: m.rows.map((r) => +(sign * share(r, label) * (half ? 0.5 : 1)).toFixed(2)),
    });
    return {
      ...base,
      legend: { ...legend(true), data: SENTIMENT_ORDER },
      grid: { left: 8, right: 16, top: 8, bottom: 36, containLabel: true },
      xAxis: { type: "value", min: -100, max: 100, ...axisStyle, axisLabel: { color: theme.muted, formatter: (v: number) => `${Math.abs(v)}%` } },
      yAxis: { type: "category", inverse: true, data: m.rows.map((r) => r.topic), ...axisStyle, axisLabel: { color: theme.muted, width: 140, overflow: "truncate" } },
      series: [
        seg("neutral", -1, true), seg("negative", -1), seg("very negative", -1),
        seg("neutral", 1, true), seg("positive", 1), seg("very positive", 1),
      ],
      tooltip: {
        ...base.tooltip,
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params: unknown) => {
          const r = m.rows[first(params).dataIndex];
          const lines = SENTIMENT_ORDER.map((l) => `${esc(l)}: ${Math.round(r.counts.get(l) ?? 0)} of ${r.total}`);
          lines.push(`<strong>${Math.round(r.favourable)}% positive or very positive</strong>`);
          return tipHtml(r.topic, lines, []);
        },
      },
    } as EChartsOption;
  }

  if (m.kind === "pie") {
    const total = m.rows.reduce((s, r) => s + r.value, 0);
    const palette = categoryColors(m.rows.map((r) => r.name));
    return {
      ...base,
      legend: legend(true),
      // The whole, in the hole: every slice then reads as a share of a number
      // you can see rather than of an unstated whole.
      title: m.donut
        ? {
            text: total.toLocaleString(),
            subtext: p.unit || "total",
            left: "center",
            top: "36%",
            textStyle: { color: theme.text, fontSize: 22, fontWeight: 650 },
            subtextStyle: { color: theme.muted },
          }
        : undefined,
      series: [
        {
          type: "pie",
          radius: m.donut ? ["42%", "68%"] : [0, "68%"],
          center: ["50%", "46%"],
          itemStyle: { borderColor: theme.background, borderWidth: 2 },
          // Inside the slice or not at all: a small slice's floating label
          // read as a stray number. The legend and tooltip name every slice.
          label: {
            position: "inside",
            color: "#fff",
            formatter: (x: { percent?: number }) => ((x.percent ?? 0) >= 9 ? `${Math.round(x.percent ?? 0)}%` : ""),
          },
          data: m.rows.map((r, i) => ({ name: r.name, value: r.value, itemStyle: { color: color(pick(palette, r.name, i)) } })),
        },
      ],
      tooltip: {
        ...base.tooltip,
        trigger: "item",
        formatter: (params: unknown) => {
          const x = first(params);
          const r = m.rows[x.dataIndex];
          const rows = detailsFor(details, { blanks, group: m.groupBy ? r.group : null, groupBy: m.groupBy, bucket: r.bucket ?? null, period: p.period });
          return tipHtml(r.name, [esc(withUnit(r.value, p.unit))], rows, m.groupBy, x.percent);
        },
      },
    } as EChartsOption;
  }

  if (m.kind === "rank") {
    const total = m.rows.reduce((s, r) => s + r.value, 0);
    // Colour per category while the palette can keep them distinct; past
    // that the axis labels name the bars and colour would only repeat.
    const palette = m.rows.length <= SERIES_COLORS.length ? categoryColors(m.rows.map((r) => r.name)) : null;
    const names = m.rows.map((r) => r.name);
    const zoom = m.rows.length > ZOOM_AFTER;
    const catAxis = {
      type: "category" as const,
      data: names,
      ...axisStyle,
      inverse: m.horizontal, // largest on top
      axisLabel: { color: theme.muted, hideOverlap: !m.horizontal, width: m.horizontal ? 140 : 90, overflow: "truncate" as const, interval: 0 },
    };
    const valAxis = { type: "value" as const, minInterval, ...axisStyle };
    return {
      ...base,
      legend: { show: false },
      grid: { left: 8, right: m.horizontal ? 48 : 16, top: 24, bottom: 8, containLabel: true },
      xAxis: m.horizontal ? valAxis : catAxis,
      yAxis: m.horizontal ? catAxis : valAxis,
      dataZoom: zoom
        ? [{ type: "slider", yAxisIndex: 0, orient: "vertical", right: 0, startValue: 0, endValue: ZOOM_AFTER - 1 }, { type: "inside", yAxisIndex: 0 }]
        : undefined,
      series: [
        {
          type: "bar",
          name: p.unit || "Value",
          barMaxWidth: 48,
          label: { show: true, position: m.horizontal ? "right" : "top", color: theme.text, formatter: (x: { value?: unknown }) => Number(x.value).toLocaleString() },
          itemStyle: { borderRadius: m.horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0] },
          data: m.rows.map((r, i) => ({
            value: r.value,
            itemStyle: { color: color(palette ? pick(palette, r.name, i) : SERIES_COLORS[0]) },
          })),
        },
      ],
      tooltip: {
        ...base.tooltip,
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params: unknown) => {
          const r = m.rows[first(params).dataIndex];
          const rows = detailsFor(details, { blanks, group: r.group ?? null, groupBy: m.groupBy, bucket: r.bucket ?? null, period: p.period });
          return tipHtml(r.name, [esc(withUnit(r.value, p.unit))], rows, m.groupBy, m.rows.length > 1 && total > 0 ? (r.value / total) * 100 : undefined);
        },
      },
    } as EChartsOption;
  }

  // Axis charts: line, area, bar, grouped, stacked, combo.
  const palette = categoryColors(m.series);
  const comboUnits = m.combo ? m.series.slice(0, 2).map(unitFor) : [];
  const axisName = (i: number) => {
    const u = comboUnits[i];
    return u ? `${m.series[i]} (${u})` : m.series[i];
  };
  const single = m.series.length === 1;
  const zoom = m.buckets.length > ZOOM_AFTER;
  const series = m.series.map((name, si) => {
    const asLine = m.combo ? si > 0 : m.shape !== "bar";
    const c = color(pick(palette, name, si));
    const showValues = single && m.buckets.length <= (asLine ? 8 : 12);
    return {
      type: asLine ? ("line" as const) : ("bar" as const),
      name: name || p.unit || "Value",
      yAxisIndex: m.combo && si > 0 ? 1 : 0,
      stack: m.stacked ? "total" : undefined,
      barMaxWidth: 48,
      smooth: false,
      symbolSize: 6,
      itemStyle: { color: c, ...(asLine ? {} : { borderRadius: m.stacked ? 0 : [4, 4, 0, 0] }) },
      lineStyle: asLine ? { width: 2 } : undefined,
      // A fill under a single trend gives three points a shape; overlapping
      // fills hide each other, so only area charts fill several.
      areaStyle: asLine && !m.combo && (m.shape === "area" || single) ? { opacity: m.shape === "area" ? 0.25 : 0.12 } : undefined,
      label: showValues ? { show: true, position: "top" as const, color: theme.text, formatter: (x: { value?: unknown }) => Number(x.value).toLocaleString() } : undefined,
      data: m.buckets.map((b) => m.at(b, name)),
    };
  });
  const yAxis = m.combo
    ? [0, 1].map((i) => ({
        type: "value" as const,
        name: axisName(i),
        nameTextStyle: { color: theme.muted },
        position: i === 0 ? ("left" as const) : ("right" as const),
        minInterval,
        ...axisStyle,
        splitLine: i === 0 ? axisStyle.splitLine : { show: false },
      }))
    : { type: "value" as const, minInterval, ...axisStyle };
  return {
    ...base,
    legend: legend(m.series.length > 1),
    grid: { left: 8, right: 16, top: m.combo ? 36 : 24, bottom: (m.series.length > 1 ? 28 : 0) + (zoom ? 40 : 8), containLabel: true },
    xAxis: { type: "category", data: m.labels, boundaryGap: m.shape === "bar" || m.combo, ...axisStyle },
    yAxis,
    dataZoom: zoom
      ? [{ type: "slider", xAxisIndex: 0, bottom: m.series.length > 1 ? 28 : 4, startValue: m.buckets.length - ZOOM_AFTER, endValue: m.buckets.length - 1 }, { type: "inside", xAxisIndex: 0 }]
      : undefined,
    series,
    tooltip: {
      ...base.tooltip,
      trigger: "axis",
      axisPointer: { type: m.shape === "bar" ? "shadow" : "line" },
      formatter: (params: unknown) => {
        const list = (Array.isArray(params) ? params : [params]) as TipParam[];
        const idx = list[0].dataIndex;
        const bucket = m.buckets[idx];
        const lines = list.map((x) => {
          const name = m.series[x.seriesIndex ?? 0] ?? "";
          const v = withUnit(Number(x.value ?? 0), unitFor(name));
          return `${typeof x.marker === "string" ? x.marker : ""}${name ? `${esc(name)}: ` : ""}${esc(v)}`;
        });
        const rows = detailsFor(details, { blanks, bucket, period: p.period, groupBy: null });
        return tipHtml(m.labels[idx], lines, rows);
      },
    },
  } as EChartsOption;
}

/** Rendered height, so a long ranking grows instead of squashing its rows. */
export function chartHeight(p: ChartProps): number {
  const m = model(p);
  if (m.kind === "pie") return 320;
  if (m.kind === "heatmap") return Math.max(220, m.ys.length * 28 + 110);
  if (m.kind === "diverging") return Math.max(200, m.rows.length * 30 + 60);
  if (m.kind === "rank" && m.horizontal) return Math.min(m.rows.length, ZOOM_AFTER) * 26 + 50;
  return 280;
}

/** The chart's numbers as a table: the accessible fallback and the CSV.
 *  `units[i]` is the unit of column i (none for the label column). */
export function toTable(p: ChartProps): { head: string[]; rows: (string | number)[][]; units: (string | undefined)[] } {
  const m = model(p);
  if (m.kind === "diverging") {
    return {
      head: ["Topic", ...SENTIMENT_ORDER, "Total"],
      rows: m.rows.map((r) => [r.topic, ...SENTIMENT_ORDER.map((l) => r.counts.get(l) ?? 0), r.total]),
      units: [],
    };
  }
  if (m.kind === "heatmap") {
    return {
      head: ["", ...m.xs],
      rows: m.ys.map((y) => [y, ...m.xs.map((x) => m.cells.get(`${x}\u0000${y}`) ?? 0)]),
      units: [undefined, ...m.xs.map(() => p.unit)],
    };
  }
  if (m.kind === "pie" || m.kind === "rank") {
    return { head: ["Category", p.unit || "Value"], rows: m.rows.map((r) => [r.name, r.value]), units: [undefined, p.unit] };
  }
  const names = m.series.map((s) => s || p.unit || "Value");
  return {
    head: [m.timeAxis ? "Period" : "Category", ...names],
    rows: m.buckets.map((b, i) => [m.labels[i], ...m.series.map((s) => m.at(b, s))]),
    units: [undefined, ...m.series.map((s) => p.seriesUnits?.[s] || p.unit)],
  };
}
