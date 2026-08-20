const CLASS: Record<string, string> = {
  succeeded: "badge-verified",
  running: "badge-pending",
  queued: "badge-pending",
  failed: "",
};

const LABEL: Record<string, string> = {
  // NOT "Up to date" — this only ever describes the last successful sync
  // job's own record (see ConnectionCard's `showDocsJobBadge`), which says
  // nothing about whether anything has changed since. "Up to date" is
  // reserved for `docsCheckedFresh`, where a live Check just confirmed it.
  succeeded: "Last synced",
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
