/**
 * Chart colour: which category gets which swatch.
 *
 * Its own module, with no JSX, for two reasons. It is pure logic with a defect
 * history -- colours came from the ARRAY INDEX, so "Done" was teal in one
 * chart and orange in the next, and a repository changed colour when another
 * appeared -- and a module without JSX can be executed directly by
 * `node --experimental-strip-types`, so the check in `__checks__` imports the
 * real implementation without this frontend growing a test stack or a build
 * dependency (CLAUDE.md: do not add a React test stack as a side effect of a
 * chart).
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
 * A category's colour, stable and meaningful.
 *
 * Index-based colouring was a real defect, not a style choice: the index came
 * from the order rows arrived, so "Done" was teal in one chart and orange in
 * the next, and the same repository changed colour when a new one appeared.
 * A legend whose colours move between charts cannot be learned.
 *
 * Known categories get a MEANING (done is green, canceled is red, blocked is
 * red) because a lifecycle chart where "Canceled" is reassuring green is
 * actively misleading. Everything else hashes its own name, so it is at least
 * the same colour every time, in every chart, across reloads.
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
 * Colours for one chart's categories: meaningful, stable, and DISTINCT.
 *
 * Hashing alone gave stability but collided -- two of four Notion pages, and
 * two of three repositories, landed on the same swatch, so a chart had
 * neighbouring bars the same colour. So a name asks for its hashed slot and
 * takes the next free one when that slot is gone: deterministic for a given
 * set of names, and never a duplicate until the palette genuinely runs out.
 */
export function categoryColors(names: (string | null | undefined)[]): Map<string, string> {
  const out = new Map<string, string>();
  const used = new Set<string>();

  // Semantic categories are assigned FIRST and are never displaced: "done is
  // green" outranks "this bar wanted green", and a lifecycle chart where
  // Canceled is a reassuring green is actively misleading.
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
