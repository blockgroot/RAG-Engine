const CLASS: Record<string, string> = {
  succeeded: "badge-verified",
  running: "badge-pending",
  queued: "badge-pending",
  failed: "",
};

export function JobStatusBadge({ status }: { status: string }) {
  const style = status === "failed" ? { color: "var(--provenance-none)", borderColor: "var(--provenance-none)" } : undefined;
  return (
    <span className={`badge ${CLASS[status] || ""}`} style={style}>
      {status}
    </span>
  );
}
