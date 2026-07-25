import { api, ConnectionRecord, JobRecord } from "@/lib/api";
import { JobStatusBadge } from "./JobStatusBadge";

const PROVIDER_LABELS: Record<string, string> = {
  notion: "Notion",
  google: "Google Drive",
  github: "GitHub",
};

/**
 * Provider-agnostic from day one: only "notion" is wired to a real connect
 * button today, but Google/GitHub render as "coming soon" through the SAME
 * component — adding them later is a config entry, not a new component,
 * mirroring the backend's factory.py extension pattern (app/auth/factory.py).
 *
 * ``lastJob`` shows the most recent sync's outcome inline (status, doc count,
 * error) so a failed/queued sync is never silently invisible — without a
 * dedicated "sync history" page cluttering the nav for something that's
 * genuinely a once-in-a-while admin action.
 */
export function ConnectionCard({
  provider,
  connection,
  lastJob,
  onIngest,
}: {
  provider: "notion" | "google" | "github";
  connection: ConnectionRecord | undefined;
  lastJob?: JobRecord;
  onIngest: (connectionId: string) => void;
}) {
  const available = provider === "notion";

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "1rem" }}>
        <h3 style={{ fontSize: "1.05rem" }}>{PROVIDER_LABELS[provider]}</h3>
        {connection ? (
          <span className="badge badge-verified">Connected</span>
        ) : (
          <span className="badge">{available ? "Not connected" : "Coming soon"}</span>
        )}
      </div>

      {connection && (
        <p className="muted" style={{ marginTop: "0.4rem" }}>
          {connection.external_workspace_name || "Connected workspace"} · since{" "}
          {new Date(connection.created_at).toLocaleDateString()}
        </p>
      )}

      {lastJob && (
        <p className="muted" style={{ marginTop: "0.4rem", display: "flex", alignItems: "center", gap: "0.5rem" }}>
          <JobStatusBadge status={lastJob.status} />
          Last synced{" "}
          {lastJob.finished_at
            ? new Date(lastJob.finished_at).toLocaleString()
            : new Date(lastJob.created_at).toLocaleString()}
          {lastJob.status === "succeeded" && lastJob.doc_count !== null && ` · ${lastJob.doc_count} documents`}
          {lastJob.status === "failed" && lastJob.error && ` · ${lastJob.error}`}
        </p>
      )}

      <div style={{ marginTop: "1rem", display: "flex", gap: "0.6rem" }}>
        {available && !connection && (
          <a className="button" href={api.connectUrl(provider)}>
            Connect {PROVIDER_LABELS[provider]}
          </a>
        )}
        {connection && (
          <button className="button button-secondary" onClick={() => onIngest(connection.id)}>
            Sync now
          </button>
        )}
      </div>
    </div>
  );
}
