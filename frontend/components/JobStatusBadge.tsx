const CLASS: Record<string, string> = {
  succeeded: "badge-verified",
  running: "badge-pending",
  queued: "badge-pending",
  failed: "",
};

const LABEL: Record<string, string> = {
  succeeded: "Up to date",
  running: "Updating",
  queued: "Queued",
  failed: "Didn’t finish",
};

export function JobStatusBadge({ status }: { status: string }) {
  const style =
    status === "failed"
      ? { color: "var(--provenance-none)", borderColor: "var(--provenance-none)" }
      : undefined;
  return (
    <span className={`badge ${CLASS[status] || ""}`} style={style}>
      {LABEL[status] || status}
    </span>
  );
}
