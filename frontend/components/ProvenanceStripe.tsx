const LABELS: Record<string, string> = {
  policy: "Company documents",
  workspace: "Workspace content",
  web: "Web search",
  github: "GitHub",
  slack: "Slack conversation",
  none: "No answer found",
};

const COLORS: Record<string, string> = {
  policy: "var(--provenance-policy)",
  // Falls back to the policy color until a dedicated design pass picks a
  // distinct one — placeholder wiring, not the intended final look.
  workspace: "var(--provenance-workspace, var(--provenance-policy))",
  web: "var(--provenance-web)",
  github: "var(--provenance-github)",
  slack: "var(--provenance-slack, var(--provenance-policy))",
  none: "var(--provenance-none)",
};

/** Small color-coded pill so a reader can tell at a glance whether an answer
 * came from company policy, Slack, GitHub, a web search, or wasn't found at all. */
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
