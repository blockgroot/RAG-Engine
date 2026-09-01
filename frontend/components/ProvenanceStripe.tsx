const LABELS: Record<string, string> = {
  policy: "Company documents",
  workspace: "Workspace content",
  web: "Web search",
  github: "GitHub",
  slack: "Slack conversation",
  linear: "Linear",
  notion: "Notion",
  google: "Google Drive",
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
  linear: "var(--provenance-linear, var(--provenance-policy))",
  notion: "var(--provenance-notion, var(--provenance-policy))",
  google: "var(--provenance-google, var(--provenance-policy))",
  none: "var(--provenance-none)",
};

/** Which agent answered, named. `agent` is the routed agent key; `source` is
 * what the answer was grounded on. They differ in exactly one case that
 * matters: the web fallback, where a source agent was asked but answered from
 * an external search — so `source` wins there, or the pill would claim Notion
 * grounded something Notion never saw.
 *
 * Colour is keyed on whichever identity is displayed, so the pill stays
 * consistent with its own text. */
const AGENT_NAMES: Record<string, string> = {
  policy: "Docs agent",
  workspace: "Workspace agent",
  github: "GitHub agent",
  slack: "Slack agent",
  linear: "Linear agent",
  notion: "Notion agent",
  google: "Drive agent",
};

export function ProvenanceStripe({
  source,
  agent,
}: {
  source: string;
  agent?: string;
}) {
  // A refusal ("none") names no source, and neither should the pill: the
  // routed agent is a diagnostic, not a provenance claim, when nothing was
  // grounded.
  const grounded = source !== "none";
  const identity = source === "web" || !grounded ? source : agent || source;
  const color = COLORS[identity] || COLORS.none;
  const label = LABELS[identity] || LABELS.none;
  const agentName = grounded && source !== "web" ? AGENT_NAMES[agent || ""] : undefined;

  return (
    <span
      className="provenance-pill"
      style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >
      <span className="provenance-dot" style={{ background: color }} />
      {label}
      {agentName && <span className="provenance-agent">{agentName}</span>}
    </span>
  );
}
