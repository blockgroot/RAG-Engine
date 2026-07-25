const LABELS: Record<string, string> = {
  policy: "Company policy",
  web: "Web search",
  none: "No answer found",
};

const COLORS: Record<string, string> = {
  policy: "var(--provenance-policy)",
  web: "var(--provenance-web)",
  none: "var(--provenance-none)",
};

/** Small color-coded pill so a reader can tell at a glance whether an answer
 * came from company policy, a web search, or wasn't found at all. */
export function ProvenanceStripe({ source }: { source: string }) {
  const color = COLORS[source] || COLORS.none;
  return (
    <span
      className="provenance-pill"
      style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >
      <span className="provenance-dot" style={{ background: color }} />
      {LABELS[source] || LABELS.none}
    </span>
  );
}
