import { ChatDonePayload } from "@/lib/sse";

/** Citations rendered as a first-class object, not a footnote — each source
 * chunk gets its own bordered strip with a monospace locator. */
export function CitationPanel({ citations }: { citations: ChatDonePayload["citations"] }) {
  if (!citations || citations.length === 0) return null;
  return (
    <div className="stack" style={{ marginTop: "var(--space-2)" }}>
      <p className="muted" style={{ fontSize: "0.8rem", textTransform: "uppercase", letterSpacing: "0.03em" }}>
        Sources
      </p>
      {citations.map((c, i) => (
        <div
          key={i}
          style={{
            borderLeft: "3px solid var(--border)",
            paddingLeft: "0.8rem",
          }}
        >
          <p style={{ margin: 0, fontSize: "0.9rem" }}>{c.content}</p>
          <p className="mono muted" style={{ margin: "0.2rem 0 0" }}>
            [{i + 1}] {c.reference}
            {c.score !== null ? ` · score ${c.score.toFixed(2)}` : ""}
          </p>
        </div>
      ))}
    </div>
  );
}
