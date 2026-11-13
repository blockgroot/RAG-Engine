/**
 * Chart colour: which category gets which swatch. No JSX, so the check in
 * `__checks__` runs the real code under node with no test stack.
 */

/** Distinct enough to tell series apart, muted enough to sit in the page. */
export const SERIES_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
];

/**
 * A category's colour, stable across charts (never the row index). Known
 * states get a meaning (done green, canceled/blocked red); others hash their name.
 */
export const CATEGORY_COLORS: Record<string, string> = {
  // Linear / issue lifecycle
  done: "#0f766e",
  completed: "#0f766e",
  closed: "#0f766e",
  "in progress": "#2563eb",
  started: "#2563eb",
  "in review": "#7c3aed",
  todo: "#64748b",
  backlog: "#94a3b8",
  triage: "#b45309",
  blocked: "#be123c",
  canceled: "#be123c",
  cancelled: "#be123c",
  duplicate: "#94a3b8",
  // GitHub review verdicts and pull-request states
  approved: "#0f766e",
  changes_requested: "#be123c",
  commented: "#64748b",
  dismissed: "#94a3b8",
  merged: "#7c3aed",
  open: "#2563eb",
};

function preferredIndex(key: string): number {
  // djb2, so a name lands on the same swatch every render. Not for security --
  // for stability.
  let hash = 5381;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 33 + key.charCodeAt(i)) | 0;
  return Math.abs(hash) % SERIES_COLORS.length;
}

/**
 * Colours for one chart: meaningful, stable and DISTINCT. A name takes its
 * hashed slot, or the next free one, so no duplicate until the palette runs out.
 */
export function categoryColors(names: (string | null | undefined)[]): Map<string, string> {
  const out = new Map<string, string>();
  const used = new Set<string>();

  // Semantic categories first, never displaced: "done is green" outranks a hash.
  for (const raw of names) {
    const key = (raw || "").trim().toLowerCase();
    const known = CATEGORY_COLORS[key];
    if (known && !out.has(key)) {
      out.set(key, known);
      used.add(known);
    }
  }

  for (const raw of names) {
    const key = (raw || "").trim().toLowerCase();
    if (out.has(key)) continue;
    const start = key ? preferredIndex(key) : 0;
    let color = SERIES_COLORS[start];
    for (let step = 0; step < SERIES_COLORS.length && used.has(color); step += 1) {
      color = SERIES_COLORS[(start + step + 1) % SERIES_COLORS.length];
    }
    out.set(key, color);
    used.add(color);
  }
  return out;
}

export function pick(
  palette: Map<string, string>,
  name: string | null | undefined,
  index = 0,
): string {
  return (
    palette.get((name || "").trim().toLowerCase()) ??
    SERIES_COLORS[index % SERIES_COLORS.length]
  );
}


/**
 * Keep the largest categories and fold the rest into a stated "Other": the
 * palette has six swatches, and silently omitting categories misleads.
 */
export function cappedCategories<T extends { name: string; value: number }>(
  rows: T[],
  max = SERIES_COLORS.length,
): { name: string; value: number }[] {
  if (rows.length <= max) return rows;
  const sorted = [...rows].sort((a, b) => b.value - a.value);
  const kept = sorted.slice(0, max - 1);
  const rest = sorted.slice(max - 1);
  const other = rest.reduce((sum, r) => sum + r.value, 0);
  return [
    ...kept,
    { name: `Other (${rest.length})`, value: other },
  ];
}
