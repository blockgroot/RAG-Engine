const LABELS: Record<string, string> = {
  policy: "From your policy documents",
  web: "From a web search — not your organization's policies",
  none: "No answer found",
};

const COLORS: Record<string, string> = {
  policy: "var(--provenance-policy)",
  web: "var(--provenance-web)",
  none: "var(--provenance-none)",
};

/** Color-coded left-edge stripe on every answer card — mirrors the CLI's
 * source-provenance styling so an answer's origin is never ambiguous. */
export function ProvenanceStripe({ source }: { source: string }) {
  const color = COLORS[source] || COLORS.none;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.6rem" }}>
      <span
        style={{
          display: "inline-block",
          width: "10px",
          height: "10px",
          borderRadius: "50%",
          background: color,
        }}
      />
      <span className="muted" style={{ fontSize: "0.8rem" }}>
        {LABELS[source] || LABELS.none}
      </span>
    </div>
  );
}
