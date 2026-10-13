"use client";

/**
 * The only file that touches ECharts at runtime. Loaded through next/dynamic
 * (ssr: false) from Chart.tsx, so a text-only answer never downloads it.
 *
 * Tree-shaken: `echarts/core` plus the three chart types and the components
 * toOption.ts emits. Anything new toOption uses must be registered here or
 * ECharts silently ignores that part of the option.
 */
import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { BarChart, HeatmapChart, LineChart, PieChart } from "echarts/charts";
import {
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";
import { SERIES_COLORS } from "../chartColors";
import { DEFAULT_THEME, type ChartTheme } from "./toOption";

echarts.use([
  BarChart,
  HeatmapChart,
  LineChart,
  PieChart,
  AriaComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  VisualMapComponent,
  SVGRenderer,
]);

/** The page's own CSS variables, so a chart is part of the design system and
 *  follows whatever the stylesheet says for light or dark. */
function readTheme(): ChartTheme {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  const colors: Record<string, string> = {};
  for (const token of SERIES_COLORS) {
    const name = token.slice(4, -1); // "var(--chart-1)" -> "--chart-1"
    colors[token] = v(name, DEFAULT_THEME.colors[token]);
  }
  return {
    text: v("--ink", DEFAULT_THEME.text),
    muted: v("--ink-muted", DEFAULT_THEME.muted),
    grid: v("--chart-grid", DEFAULT_THEME.grid),
    border: v("--border-strong", DEFAULT_THEME.border),
    background: v("--surface-elevated", DEFAULT_THEME.background),
    fontFamily: v("--font-body", "") || undefined,
    colors,
    reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  };
}

export default function EChart({
  build,
  height,
  label,
  onInstance,
}: {
  /** Theme -> option. A function, because the theme is only known here. */
  build: (theme: ChartTheme) => EChartsOption;
  height: number;
  label: string;
  onInstance?: (chart: echarts.ECharts | null) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const [theme, setTheme] = useState<ChartTheme | null>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const chart = echarts.init(node, null, { renderer: "svg" });
    chartRef.current = chart;
    onInstance?.(chart);
    setTheme(readTheme());

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(node);
    const media = [
      window.matchMedia("(prefers-color-scheme: dark)"),
      window.matchMedia("(prefers-reduced-motion: reduce)"),
    ];
    const refresh = () => setTheme(readTheme());
    media.forEach((m) => m.addEventListener("change", refresh));
    return () => {
      media.forEach((m) => m.removeEventListener("change", refresh));
      observer.disconnect();
      onInstance?.(null);
      chart.dispose();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (theme && chartRef.current) chartRef.current.setOption(build(theme), { notMerge: true });
  }, [build, theme]);

  useEffect(() => {
    chartRef.current?.resize();
  }, [height]);

  return <div ref={ref} className="chart-canvas" style={{ height }} role="img" aria-label={label} />;
}
