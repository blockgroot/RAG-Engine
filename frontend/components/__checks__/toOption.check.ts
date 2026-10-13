/**
 * The runnable check on chart shapes. From `frontend/`:
 *   npm run check:charts
 *
 * Imports the REAL `toOption` under `node --experimental-strip-types`. That
 * module imports `../chartColors` without an extension (Next rejects `.ts`
 * specifiers), so a resolve hook retries a relative specifier with `.ts`.
 */
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, next) {
    try {
      return next(specifier, context);
    } catch (error) {
      if (specifier.startsWith(".") && !specifier.endsWith(".ts")) return next(`${specifier}.ts`, context);
      throw error;
    }
  },
});

const { toOption } = await import("../charts/toOption.ts");

let failed = 0;
const check = (ok: boolean, label: string) => {
  if (ok) console.log(`ok   ${label}`);
  else {
    console.error(`FAIL ${label}`);
    failed += 1;
  }
};
const arr = (x: unknown): any[] => (x == null ? [] : Array.isArray(x) ? x : [x]);

// 1. Combo: two value axes, each named with its unit; bar left, line right.
{
  const o: any = toOption({
    chart: "combo",
    period: "month",
    points: [
      { bucket: "2026-01-01", group: "Revenue", value: 120 },
      { bucket: "2026-01-01", group: "Margin", value: 12 },
      { bucket: "2026-02-01", group: "Revenue", value: 140 },
      { bucket: "2026-02-01", group: "Margin", value: 15 },
    ],
    seriesUnits: { Revenue: "₹ lakh", Margin: "%" },
  });
  const y = arr(o.yAxis);
  check(y.length === 2, "combo has two y axes");
  check(String(y[0]?.name).includes("₹ lakh") && String(y[1]?.name).includes("%"), "combo axes named with their units");
  const s = arr(o.series);
  check(s[0]?.type === "bar" && s[0]?.yAxisIndex === 0 && s[1]?.type === "line" && s[1]?.yAxisIndex === 1, "combo: bar left, line right");
}

// 2. Stacked bar sets `stack` on every series.
{
  const o: any = toOption({
    chart: "stacked_bar",
    period: "week",
    points: [
      { bucket: "2026-01-05", group: "Done", value: 3 },
      { bucket: "2026-01-05", group: "Todo", value: 2 },
      { bucket: "2026-01-12", group: "Done", value: 1 },
    ],
  });
  check(arr(o.series).length === 2 && arr(o.series).every((s) => s.stack), "stacked_bar stacks");
}

// 3. Quarters stay in time order even when the server sends them by value.
{
  const o: any = toOption({
    chart: "bar",
    period: "quarter",
    points: [
      { bucket: "2026-07-01", group: null, value: 9 },
      { bucket: "2026-01-01", group: null, value: 5 },
      { bucket: "2026-04-01", group: null, value: 1 },
    ],
  });
  const labels = arr(o.xAxis)[0]?.data;
  check(JSON.stringify(labels) === JSON.stringify(["Q1 2026", "Q2 2026", "Q3 2026"]), `quarters in time order (${labels})`);
}

// 4. More than 24 buckets adds a dataZoom slider showing the latest.
{
  const points = Array.from({ length: 30 }, (_, i) => ({
    bucket: new Date(Date.UTC(2026, 0, 1 + i)).toISOString().slice(0, 10),
    group: null,
    value: i,
  }));
  const o: any = toOption({ chart: "line", period: "day", points });
  const zoom = arr(o.dataZoom);
  check(zoom.some((z) => z.type === "slider"), ">24 buckets adds a dataZoom slider");
  const few: any = toOption({ chart: "line", period: "day", points: points.slice(0, 10) });
  check(arr(few.dataZoom).length === 0, "<=24 buckets has no dataZoom");
}

// 5. More than 8 categories becomes a horizontal bar, ranked by value.
{
  const points = Array.from({ length: 10 }, (_, i) => ({ bucket: "2026-01-01", group: `Person ${i}`, value: (i * 7) % 10 }));
  const o: any = toOption({ chart: "bar", period: "month", groupBy: "actor", points });
  const y = arr(o.yAxis)[0];
  check(y?.type === "category" && arr(o.xAxis)[0]?.type === "value", ">8 categories -> horizontal");
  const values = arr(o.series)[0].data.map((d: any) => d.value);
  check(values.every((v: number, i: number) => i === 0 || values[i - 1] >= v), "horizontal ranking sorted by value");
  const four: any = toOption({ chart: "bar", period: "month", groupBy: "actor", points: points.slice(0, 4) });
  check(arr(four.xAxis)[0]?.type === "category", "<=8 categories stay vertical");
}

// 6. A line of two buckets is drawn as bars; donut carries its total.
{
  const o: any = toOption({
    chart: "line",
    period: "month",
    points: [
      { bucket: "2026-01-01", group: null, value: 1 },
      { bucket: "2026-02-01", group: null, value: 2 },
    ],
  });
  check(arr(o.series)[0]?.type === "bar", "line with <3 buckets renders as bar");
  const d: any = toOption({
    chart: "donut",
    period: "month",
    groupBy: "state",
    points: [
      { bucket: "2026-01-01", group: "Done", value: 6 },
      { bucket: "2026-01-01", group: "Todo", value: 4 },
    ],
  });
  check(arr(d.title)[0]?.text === "10" && Array.isArray(arr(d.series)[0]?.radius), "donut shows its total in the centre");
}

{
  // Two breakdowns: columns are the first, rows the second, summed over time.
  const h: any = toOption({
    chart: "heatmap",
    period: "month",
    groupBy: "c0",
    splitBy: "c1",
    points: [
      { bucket: "", group: "North", series: "Q1", value: 3 },
      { bucket: "", group: "North", series: "Q2", value: 5 },
      { bucket: "", group: "South", series: "Q1", value: 2 },
    ],
  });
  const data = arr(h.series)[0]?.data ?? [];
  check(arr(h.xAxis)[0]?.data?.join() === "North,South" && arr(h.yAxis)[0]?.data?.join() === "Q1,Q2",
    "heatmap: first breakdown across, second down");
  check(data.some((d: number[]) => d[0] === 1 && d[1] === 1 && d[2] === 0), "heatmap: an empty cell is drawn as 0");
  check(arr(h.visualMap)[0]?.max === 5, "heatmap: colour scale tops out at the largest cell");

  const sa: any = toOption({
    chart: "stacked_area",
    period: "month",
    groupBy: "actor",
    points: ["2026-01-01", "2026-02-01", "2026-03-01"].flatMap((b) => [
      { bucket: b, group: "ana", value: 1 },
      { bucket: b, group: "raj", value: 2 },
    ]),
  });
  const ss = arr(sa.series);
  check(ss.length === 2 && ss.every((x: any) => x.type === "line" && x.stack === "total" && x.areaStyle),
    "stacked area: one filled, stacked line per series");
}

{
  // A document table: months keep the document's order, plants are the
  // series, and a stacked bar stacks -- never "March · Plant A" ranked.
  const points = ["January", "February", "March"].flatMap((m, i) => [
    { bucket: "", group: m, series: "Plant A", value: 2400 + i * 300 },
    { bucket: "", group: m, series: "Plant B", value: 2100 + i * 100 },
  ]);
  const o: any = toOption({ chart: "stacked_bar", period: "month", groupBy: "c0", splitBy: "c1",
                            points, ordered: true });
  check(JSON.stringify(arr(o.xAxis)[0]?.data) === JSON.stringify(["January", "February", "March"]),
    "document table: categories in document order on the axis");
  const s = arr(o.series);
  check(s.length === 2 && s.every((x: any) => x.stack), "document table: second breakdown is stacked series");
  const one: any = toOption({ chart: "bar", period: "month", groupBy: "c0", ordered: true, points: [
    { bucket: "", group: "January", value: 3200 }, { bucket: "", group: "February", value: 3750 },
    { bucket: "", group: "March", value: 4100 }] });
  check(JSON.stringify(arr(one.xAxis)[0]?.data) === JSON.stringify(["January", "February", "March"]),
    "document table: one breakdown keeps document order, not value order");
}

{
  // One series of a few bars: each bar its own colour. A bar with no
  // breakdown is the "Total", never "Unknown".
  const o: any = toOption({ chart: "bar", period: "month", groupBy: "c0", ordered: true, points: [
    { bucket: "", group: "January", value: 3200 }, { bucket: "", group: "February", value: 3750 },
    { bucket: "", group: "March", value: 4100 }] });
  const colors = arr(o.series)[0].data.map((d: any) => d.itemStyle?.color);
  check(new Set(colors).size === 3, "single-series bars: a colour per bar");
  const total: any = toOption({ chart: "bar", period: "month", points: [{ bucket: "", group: null, value: 11050 }] });
  const label = JSON.stringify(arr(total.xAxis)[0]?.data ?? arr(total.yAxis)[0]?.data ?? arr(total.series)[0]?.data);
  check(!label.includes("Unknown"), `no breakdown is never "Unknown" (${label})`);
}

if (failed) {
  console.error(`\n${failed} failure(s)`);
  process.exit(1);
}
console.log("\nall chart checks passed");
