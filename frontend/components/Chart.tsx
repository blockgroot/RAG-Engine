"use client";

/**
 * The chart under an answer: an ECharts drawing, its numbers as a table, and
 * PNG/CSV downloads.
 *
 * Every number rendered here was computed by SQL on the server. Shapes and
 * ordering live in `charts/toOption.ts` (pure, checked by
 * `npm run check:charts`); ECharts itself is loaded only when a chart is on
 * screen, so a text answer never pays for it.
 */
import dynamic from "next/dynamic";
import { useCallback, useMemo, useRef, useState } from "react";
import type { ECharts } from "echarts/core";
import {
  DEFAULT_THEME,
  chartHeight,
  toOption,
  toTable,
  withUnit,
  type ChartProps,
  type ChartTheme,
} from "./charts/toOption";

export { withUnit };
export type { DetailRow, Point } from "./charts/toOption";

const EChart = dynamic(() => import("./charts/EChart"), {
  ssr: false,
  loading: () => <div className="chart-skeleton" aria-hidden />,
});

/** A CSV cell. Text is quoted, and a leading = + - @ is defused: the names
 *  came from connected sources and a spreadsheet would run them as formulas. */
function csvCell(value: string | number): string {
  if (typeof value === "number") return String(value);
  const safe = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value;
  return `"${safe.replace(/"/g, '""')}"`;
}

function download(href: string, name: string) {
  const a = document.createElement("a");
  a.href = href;
  a.download = name;
  a.click();
}

/** The SVG renderer's `getDataURL` is always SVG whatever `type` says, so
 *  the PNG is rasterised here, on the card's own background. */
function svgToPng(chart: ECharts): Promise<string> {
  const bg =
    getComputedStyle(document.documentElement).getPropertyValue("--surface-elevated").trim() ||
    DEFAULT_THEME.background;
  const svg = chart.getDataURL({ backgroundColor: bg });
  const scale = 2;
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = chart.getWidth() * scale;
      canvas.height = chart.getHeight() * scale;
      const ctx = canvas.getContext("2d");
      if (!ctx) return reject(new Error("no canvas"));
      ctx.fillStyle = bg;
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      resolve(canvas.toDataURL("image/png"));
    };
    img.onerror = reject;
    img.src = svg;
  });
}

export function Chart(props: ChartProps) {
  const { chart, points, period, unit, seriesUnits, groupBy, splitBy, blankLabels, details, ordered } = props;
  const [showTable, setShowTable] = useState(false);
  const instance = useRef<ECharts | null>(null);

  const stable = useMemo<ChartProps>(
    () => ({ chart, points, period, unit, seriesUnits, groupBy, splitBy, blankLabels, details, ordered }),
    [chart, points, period, unit, seriesUnits, groupBy, splitBy, blankLabels, details, ordered],
  );
  const build = useCallback((theme: ChartTheme) => toOption(stable, theme), [stable]);
  const table = useMemo(() => toTable(stable), [stable]);
  const height = useMemo(() => chartHeight(stable), [stable]);
  const onInstance = useCallback((c: ECharts | null) => {
    instance.current = c;
  }, []);

  if (points.length === 0) return null;

  const label = `${chart.replace(/_/g, " ")} chart, ${table.rows.length} rows. Use the Table button for the numbers.`;

  return (
    <div className="chart-card">
      <div className="chart-scroll" style={{ minHeight: height }} hidden={showTable}>
        <EChart build={build} height={height} label={label} onInstance={onInstance} />
      </div>
      {showTable && (
        <div className="chart-table-wrap">
          <table className="chart-table">
            <caption className="sr-only">Chart data</caption>
            <thead>
              <tr>
                {table.head.map((h, i) => (
                  <th key={i} scope="col">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((cell, i) =>
                    i === 0 ? (
                      <th key={i} scope="row">{cell}</th>
                    ) : (
                      <td key={i}>
                        {typeof cell === "number" ? withUnit(cell, table.units[i]) : cell}
                      </td>
                    ),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {chart === "diverging_bar" && (
        <p className="chart-note">Share of responses. Neutral is split across the centre line.</p>
      )}
      <div className="chart-actions">
        <button
          type="button"
          className="chart-action"
          aria-pressed={showTable}
          onClick={() => setShowTable((v) => !v)}
        >
          {showTable ? "Chart" : "Table"}
        </button>
        <button
          type="button"
          className="chart-action"
          disabled={showTable}
          onClick={() => {
            if (instance.current) svgToPng(instance.current).then((url) => download(url, "chart.png"));
          }}
        >
          Download PNG
        </button>
        <button
          type="button"
          className="chart-action"
          onClick={() => {
            const csv = [table.head, ...table.rows].map((r) => r.map(csvCell).join(",")).join("\r\n");
            const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
            download(url, "chart.csv");
            setTimeout(() => URL.revokeObjectURL(url), 0);
          }}
        >
          Download CSV
        </button>
      </div>
    </div>
  );
}
