import { useState } from "react";
import { api, ConnectionRecord, JobRecord, SyncChanges } from "@/lib/api";
import { JobStatusBadge } from "./JobStatusBadge";

const PROVIDER_LABELS: Record<string, string> = {
  notion: "Notion",
  google: "Google Drive",
  github: "GitHub",
};

const SOURCE_NOUN: Record<string, string> = {
  notion: "Notion",
  google: "Drive",
  github: "GitHub",
};

const ACTIVE = new Set(["queued", "running"]);

/**
 * Provider card: connected sources no longer show a blunt "Sync now" that
 * re-dumps everything. Instead we surface a change notice when remote pages
 * differ, and "Update policies" runs an incremental upsert.
 *
 * Google also needs a folder URL before any sync — Drive has no Notion-style
 * "whatever was shared with the integration" boundary.
 */
export function ConnectionCard({
  provider,
  connection,
  lastJob,
  changes,
  checkingChanges,
  onUpdate,
  onCheckAgain,
  onConfigSaved,
  workspaceId,
}: {
  provider: "notion" | "google" | "github";
  connection: ConnectionRecord | undefined;
  lastJob?: JobRecord;
  changes?: SyncChanges | null;
  checkingChanges?: boolean;
  onUpdate: (connectionId: string) => void;
  onCheckAgain?: () => void;
  onConfigSaved?: (connection: ConnectionRecord) => void;
  /** When set, this card manages a personal sub-workspace connection instead
   * of the org-wide one (Workspace-within-a-Workspace) -- connect/config
   * calls are routed to the workspace-scoped endpoints. */
  workspaceId?: string;
}) {
  const available = provider === "notion" || provider === "google";
  const syncInProgress = lastJob != null && ACTIVE.has(lastJob.status);
  const needsUpdate = Boolean(changes?.has_changes);
  const folderConfigured = Boolean(connection?.source_config?.folder_id);
  const needsFolder = provider === "google" && connection && !folderConfigured;

  const [folderUrl, setFolderUrl] = useState("");
  const [savingConfig, setSavingConfig] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  async function handleSaveFolder(e: React.FormEvent) {
    e.preventDefault();
    if (!connection) return;
    setSavingConfig(true);
    setConfigError(null);
    try {
      const result = workspaceId
        ? await api.setWorkspaceConnectionConfig(workspaceId, connection.id, folderUrl.trim())
        : await api.setConnectionConfig(connection.id, folderUrl.trim());
      setFolderUrl("");
      onConfigSaved?.({
        ...connection,
        source_config: result.config,
      });
    } catch (err) {
      setConfigError(err instanceof Error ? err.message : "Could not save folder.");
    } finally {
      setSavingConfig(false);
    }
  }

  return (
    <div className="card">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: "1rem",
        }}
      >
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

      {connection && provider === "google" && folderConfigured && (
        <p className="muted" style={{ marginTop: "0.4rem" }}>
          Syncing folder:{" "}
          <strong style={{ color: "inherit", fontWeight: 600 }}>
            {connection.source_config?.folder_name || connection.source_config?.folder_id}
          </strong>
        </p>
      )}

      {needsFolder && (
        <form onSubmit={handleSaveFolder} className="stack" style={{ marginTop: "0.9rem" }}>
          <p className="muted" style={{ margin: 0 }}>
            Paste a Drive folder URL (or folder id). Only Google Docs under that folder
            will be ingested.
          </p>
          <div className="field">
            <label htmlFor={`folder-${provider}`}>Folder URL</label>
            <input
              id={`folder-${provider}`}
              className="input"
              type="text"
              required
              placeholder="https://drive.google.com/drive/folders/…"
              value={folderUrl}
              onChange={(e) => setFolderUrl(e.target.value)}
            />
          </div>
          {configError && <div className="banner banner-warn">{configError}</div>}
          <button className="button" type="submit" disabled={savingConfig || !folderUrl.trim()}>
            {savingConfig ? "Saving…" : "Save folder"}
          </button>
        </form>
      )}

      {lastJob && (
        <p
          className="muted"
          style={{ marginTop: "0.4rem", display: "flex", alignItems: "center", gap: "0.5rem" }}
        >
          <JobStatusBadge status={lastJob.status} />
          {ACTIVE.has(lastJob.status)
            ? "Updating changed policies…"
            : lastJob.status === "succeeded"
              ? `Last sync completed ${
                  lastJob.finished_at
                    ? new Date(lastJob.finished_at).toLocaleString()
                    : new Date(lastJob.created_at).toLocaleString()
                }${
                  lastJob.doc_count != null && lastJob.doc_count > 0
                    ? ` · ${lastJob.doc_count} page${lastJob.doc_count === 1 ? "" : "s"} refreshed`
                    : " · already up to date"
                }`
              : lastJob.status === "failed"
                ? lastJob.error || "Update failed"
                : null}
        </p>
      )}

      {connection && !needsFolder && needsUpdate && !syncInProgress && (
        <div className="banner banner-wait" style={{ marginTop: "0.9rem" }}>
          <strong>We noticed changes in your policies</strong>
          <p className="muted" style={{ margin: "0.35rem 0 0" }}>
            {[
              changes!.new_count > 0 ? `${changes!.new_count} new` : null,
              changes!.updated_count > 0 ? `${changes!.updated_count} updated` : null,
              changes!.removed_count > 0 ? `${changes!.removed_count} removed` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
            . Only those pages will be refreshed — nothing is duplicated.
          </p>
        </div>
      )}

      {connection && !needsFolder && !needsUpdate && !syncInProgress && !checkingChanges && (
        <p className="muted" style={{ marginTop: "0.75rem" }}>
          Policies look up to date
          {changes != null
            ? ` (${changes.unchanged_count} page${
                changes.unchanged_count === 1 ? "" : "s"
              } match ${SOURCE_NOUN[provider] || "source"})`
            : ""}
          . Re-sync can be done if any new changes were made to the policies.
        </p>
      )}

      {connection && checkingChanges && (
        <p className="muted" style={{ marginTop: "0.75rem" }}>
          Checking for policy updates…
        </p>
      )}

      <div style={{ marginTop: "1rem", display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
        {available && !connection && (
          <a
            className="button"
            href={workspaceId ? api.connectWorkspaceUrl(workspaceId, provider) : api.connectUrl(provider)}
          >
            Connect {PROVIDER_LABELS[provider]}
          </a>
        )}
        {connection && !needsFolder && needsUpdate && (
          <button
            className="button"
            type="button"
            onClick={() => onUpdate(connection.id)}
            disabled={syncInProgress}
          >
            {syncInProgress ? "Updating…" : "Update policies"}
          </button>
        )}
        {connection && !needsFolder && !syncInProgress && onCheckAgain && (
          <button
            className="button-secondary"
            type="button"
            onClick={onCheckAgain}
            disabled={checkingChanges}
          >
            {checkingChanges ? "Checking…" : "Check for updates"}
          </button>
        )}
      </div>
    </div>
  );
}
