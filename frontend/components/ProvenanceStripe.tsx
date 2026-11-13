import { fileKindLabel } from "@/lib/fileKind";

export const LABELS: Record<string, string> = {
  policy: "Company documents",
  workspace: "Workspace content",
  web: "Web search",
  github: "GitHub",
  slack: "Slack conversation",
  linear: "Linear",
  notion: "Notion",
  google: "Google Drive",
  forms: "Google Forms",
  insights: "Activity",
  // The asker's own upload, not a connected source.
  attachment: "Attached file",
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
  forms: "var(--provenance-forms, var(--provenance-policy))",
  insights: "var(--provenance-insights, var(--chart-1))",
  attachment: "var(--provenance-attachment, var(--accent))",
  none: "var(--provenance-none)",
};

/** Which agent answered. `source` (what grounded it) wins over `agent` for the
 * web fallback, or the pill would credit a tool that never saw it. Colour
 * follows the displayed identity. */
const AGENT_NAMES: Record<string, string> = {
  policy: "Docs agent",
  workspace: "Workspace agent",
  github: "GitHub agent",
  slack: "Slack agent",
  linear: "Linear agent",
  notion: "Notion agent",
  google: "Drive agent",
  forms: "Forms",
  insights: "Charts",
  // `attachment` deliberately absent: the label says it all.
};

export function ProvenanceStripe({
  source,
  agent,
  attachments,
  citations,
  connected,
  live,
}: {
  source: string;
  agent?: string;
  /** Tools a connected answer read; two or more are all named. */
  connected?: string[];
  /** Files that were in this prompt. */
  attachments?: string[];
  /** How many corpus chunks were also in it. */
  citations?: number;
  /** Connectors read live for this answer (Second Brain live tools). */
  live?: { provider: string; fetched_at: string }[];
}) {
  const liveChip = live && live.length > 0 ? <LiveChip live={live} /> : null;
  // An attached file OUTRANKS the routed agent, or the pill names a source the
  // answer never used. Empty citations mean the file alone was in front of the
  // model; per-sentence origin is not claimed.
  if (attachments && attachments.length > 0 && source !== "none") {
    const kind = fileKindLabel(attachments);
    const alsoCorpus = (citations ?? 0) > 0;
    const color = COLORS.attachment;
    const detail = alsoCorpus
      ? LABELS[agent || source] || LABELS.policy
      : attachments.length === 1
        ? attachments[0]
        : `${attachments.length} files`;
    return (
      <span
        className="provenance-pill"
        style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}
        title={attachments.join(", ")}
      >
        <span className="provenance-dot" style={{ background: color }} />
        {kind}
        <span className="provenance-agent provenance-file">{detail}</span>
        {liveChip}
      </span>
    );
  }

  // A refusal ("none") names no source. Insights is the exception: `source` is
  // the connector the SQL ran against.
  const grounded = source !== "none";
  // A Chart-mode reply that is not a chart is badged "Charts", never "No answer found".
  if (!grounded && agent === "insights") {
    const color = COLORS.insights;
    return (
      <span
        className="provenance-pill"
        style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}
      >
        <span className="provenance-dot" style={{ background: color }} />
        Charts
      </span>
    );
  }
  const identity =
    source === "web" || !grounded
      ? source
      : agent === "insights"
        ? source
        : agent || source;
  const color = COLORS[identity] || COLORS.none;
  const crossTool = grounded && source !== "web" && (connected?.length ?? 0) > 1;
  const label = crossTool
    ? connected!.map((p) => LABELS[p] || p).join(" + ")
    : LABELS[identity] || LABELS.none;
  const agentName = crossTool
    ? "Connected answer"
    : grounded && source !== "web"
      ? agent === "insights"
        ? "Charts"
        : AGENT_NAMES[agent || ""]
      : undefined;

  return (
    <span
      className="provenance-pill"
      style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >
      <span className="provenance-dot" style={{ background: color }} />
      {label}
      {agentName && <span className="provenance-agent">{agentName}</span>}
      {grounded && liveChip}
    </span>
  );
}

/** "· live": which connectors were read at question time, and when. Only
 * rendered from `live_sources`, which the backend fills only for a live read
 * that reached the prompt -- a synced copy is never labelled live. */
function LiveChip({ live }: { live: { provider: string; fetched_at: string }[] }) {
  const names = Array.from(new Set(live.map((l) => LABELS[l.provider] || l.provider)));
  const when = new Date(live[0].fetched_at);
  const time = isNaN(when.getTime())
    ? ""
    : when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return (
    <span
      className="provenance-agent provenance-live"
      title={`Read live from ${names.join(" and ")}${time ? ` at ${time}` : ""}`}
    >
      {names.join(" + ")} · live
    </span>
  );
}
