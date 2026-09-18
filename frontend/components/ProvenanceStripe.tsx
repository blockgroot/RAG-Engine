import { fileKindLabel } from "@/lib/fileKind";

const LABELS: Record<string, string> = {
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
  // The asker's own upload, not a connected source. Named differently on
  // purpose: every other label answers "which of our tools was this in?",
  // and this one answers "this came from the file you just gave me".
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
  forms: "Forms",
  insights: "Charts",
  // Deliberately absent: `attachment` needs no second word. The label
  // already says the whole truth, and "Attached file · Attachment agent"
  // reads as two facts where there is one.
};

export function ProvenanceStripe({
  source,
  agent,
  attachments,
  citations,
}: {
  source: string;
  agent?: string;
  /** Files that were in this prompt. */
  attachments?: string[];
  /** How many corpus chunks were also in it. */
  citations?: number;
}) {
  // A file the asker attached OUTRANKS the routed agent as the pill's
  // identity, and that is a correctness fix rather than a preference. With
  // blending, a PDF question routes to whichever source scores best and the
  // pill was printing that source's name -- "Notion" above an answer built
  // from an uploaded PDF, which is the one thing this pill exists not to do.
  //
  // What we can honestly distinguish is whether the CORPUS also contributed:
  // citations come from retrieved chunks, so an empty list means the answer
  // had only the file to work with. We do not claim to know which sentence
  // came from where — only what was in front of the model.
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
      </span>
    );
  }

  // A refusal ("none") names no source, and neither should the pill: the
  // routed agent is a diagnostic, not a provenance claim, when nothing was
  // grounded.
  // Insights is the exception the other way: `agent` is "insights" and
  // `source` is the connector the SQL ran against. Naming the agent as the
  // identity made the pill say "No answer found" on a real Drive pie.
  const grounded = source !== "none";
  const identity =
    source === "web" || !grounded
      ? source
      : agent === "insights"
        ? source
        : agent || source;
  const color = COLORS[identity] || COLORS.none;
  const label = LABELS[identity] || LABELS.none;
  const agentName =
    grounded && source !== "web"
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
    </span>
  );
}
