import { api, ConnectionRecord } from "@/lib/api";

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
 */
export function ConnectionCard({
  provider,
  connection,
  onIngest,
}: {
  provider: "notion" | "google" | "github";
  connection: ConnectionRecord | undefined;
  onIngest: (connectionId: string) => void;
}) {
  const available = provider === "notion";

  return (
    <div className="card stack">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ fontSize: "1.1rem" }}>{PROVIDER_LABELS[provider]}</h3>
        {connection ? (
          <span className="badge badge-verified">connected</span>
        ) : (
          <span className="badge">{available ? "not connected" : "coming soon"}</span>
        )}
      </div>

      {connection && (
        <p className="muted">
          {connection.external_workspace_name || connection.id} · connected{" "}
          {new Date(connection.created_at).toLocaleDateString()}
        </p>
      )}

      {available && !connection && (
        <a className="button" href={api.connectUrl(provider)}>
          Connect {PROVIDER_LABELS[provider]}
        </a>
      )}

      {connection && (
        <button className="button button-secondary" onClick={() => onIngest(connection.id)}>
          Ingest now
        </button>
      )}
    </div>
  );
}
