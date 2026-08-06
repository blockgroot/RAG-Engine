import { useState } from "react";
import { api, ConnectionRecord, JobRecord, SyncChanges } from "@/lib/api";
import { DriveFolderPicker } from "./DriveFolderPicker";
import { JobStatusBadge } from "./JobStatusBadge";
import { ProviderMark } from "./ProviderMark";

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

function friendlyWhen(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (sameDay) return `today at ${time}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const wasYesterday =
    d.getFullYear() === yesterday.getFullYear() &&
    d.getMonth() === yesterday.getMonth() &&
    d.getDate() === yesterday.getDate();
  if (wasYesterday) return `yesterday at ${time}`;
  const date = d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return `${date} at ${time}`;
}


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
  const available = provider === "notion" || provider === "google" || provider === "github";
  // GitHub is a "live" source: nothing is ever fetched, chunked, embedded, or
  // stored, so this card must hide every ingestion-shaped control (sync status,
  // change counts, Update/Check). Showing them would promise a sync that does
  // not exist -- and the API refuses those calls for GitHub anyway.
  const isLive = provider === "github";
  const syncInProgress = !isLive && lastJob != null && ACTIVE.has(lastJob.status);
  const needsUpdate = !isLive && Boolean(changes?.has_changes);
  const folderConfigured = Boolean(connection?.source_config?.folder_id);
  const needsFolder = provider === "google" && connection && !folderConfigured;

  // What the admin actually authorized on GitHub's install screen.
  const repoSelection = connection?.source_config?.repository_selection;
  const repos = connection?.source_config?.repos ?? [];
  const [refreshingScope, setRefreshingScope] = useState(false);
  const [scopeError, setScopeError] = useState<string | null>(null);

  async function refreshScope() {
    if (!connection) return;
    setRefreshingScope(true);
    setScopeError(null);
    try {
      const scope = await api.refreshConnectionScope(connection.id);
      onConfigSaved?.({
        ...connection,
        source_config: {
          ...connection.source_config,
          repository_selection: scope.repository_selection,
          repos: scope.repos,
        },
      });
    } catch (err) {
      setScopeError(err instanceof Error ? err.message : "Could not refresh repositories.");
    } finally {
      setRefreshingScope(false);
    }
  }

  const [configError, setConfigError] = useState<string | null>(null);

  return (
    <div className={`card source-studio-card source-studio-card--${provider}${connection ? " is-linked" : ""}`}>
      <div className="source-studio-top">
        <div className="source-studio-title">
          <ProviderMark provider={provider} />
          <div>
            <h3>{PROVIDER_LABELS[provider]}</h3>
            <p className="source-studio-kind">
              {isLive ? "Live answers" : "Synced documents"}
            </p>
          </div>
        </div>
        {connection ? (
          <span className="badge badge-verified">Linked</span>
        ) : (
          <span className="badge">{available ? "Not linked yet" : "Coming soon"}</span>
        )}
      </div>

      {connection && (
        <p className="muted" style={{ marginTop: "0.35rem" }}>
          {connection.external_workspace_name || "Connected"}
          {provider === "google" && folderConfigured
            ? ` · ${connection.source_config?.folder_name || "Drive folder"}`
            : ""}
        </p>
      )}

      {isLive && connection && (
        <div className="stack source-live-copy" style={{ marginTop: "0.75rem", gap: "0.4rem" }}>
          <p className="muted" style={{ margin: 0 }}>
            {repoSelection === "all"
              ? "Ready to answer questions about your repos."
              : repos.length > 0
                ? `Ready for ${repos.length} repo${repos.length === 1 ? "" : "s"}.`
                : "No repos linked yet — refresh the list, or update access on GitHub."}
          </p>
          {repoSelection !== "all" && repos.length > 0 && (
            <p className="muted source-live-repos" style={{ margin: 0 }}>
              {repos
                .slice(0, 4)
                .map((r) => r.full_name.split("/").pop() || r.full_name)
                .join(" · ")}
              {repos.length > 4 ? ` · +${repos.length - 4} more` : ""}
            </p>
          )}
          <p className="muted source-live-hint" style={{ margin: 0 }}>
            Ask in the Code tab — answers come straight from GitHub.
          </p>
          {scopeError && <div className="banner banner-warn">{scopeError}</div>}
        </div>
      )}

      {needsFolder && connection && (
        <div className="stack" style={{ marginTop: "0.9rem" }}>
          <DriveFolderPicker
            connectionId={connection.id}
            workspaceId={workspaceId}
            inputId={`folder-${provider}`}
            onSaved={(config) => {
              setConfigError(null);
              onConfigSaved?.({ ...connection, source_config: config });
            }}
            onError={(message) => setConfigError(message || null)}
          />
          {configError && <div className="banner banner-warn">{configError}</div>}
        </div>
      )}

      {lastJob && !isLive && (
        <p
          className="muted"
          style={{ marginTop: "0.55rem", display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}
        >
          <JobStatusBadge status={lastJob.status} />
          {ACTIVE.has(lastJob.status)
            ? "Updating…"
            : lastJob.status === "succeeded"
              ? (() => {
                  const when = friendlyWhen(lastJob.finished_at || lastJob.created_at);
                  const count = lastJob.doc_count;
                  if (count != null && count > 0) {
                    return `${when} · ${count} page${count === 1 ? "" : "s"}`;
                  }
                  return when;
                })()
              : lastJob.status === "failed"
                ? "Update failed — try again"
                : null}
        </p>
      )}

      {connection && !needsFolder && needsUpdate && !syncInProgress && (
        <p className="muted" style={{ marginTop: "0.65rem" }}>
          {[
            changes!.new_count > 0 ? `${changes!.new_count} new` : null,
            changes!.updated_count > 0 ? `${changes!.updated_count} edited` : null,
            changes!.removed_count > 0 ? `${changes!.removed_count} removed` : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
      )}

      {connection && checkingChanges && !isLive && (
        <p className="muted" style={{ marginTop: "0.65rem" }}>
          Checking…
        </p>
      )}

      <div className="source-card-actions">
        {available && !connection && (
          <a
            className="button"
            href={workspaceId ? api.connectWorkspaceUrl(workspaceId, provider) : api.connectUrl(provider)}
          >
            Connect {PROVIDER_LABELS[provider]}
          </a>
        )}
        {connection && isLive && !workspaceId && (
          <button
            className="button button-secondary"
            type="button"
            onClick={refreshScope}
            disabled={refreshingScope}
            title="Update the list of repos Folio can see"
          >
            {refreshingScope ? "Refreshing…" : "Refresh list"}
          </button>
        )}
        {connection && !isLive && !needsFolder && needsUpdate && (
          <button
            className="button"
            type="button"
            onClick={() => onUpdate(connection.id)}
            disabled={syncInProgress}
          >
            {syncInProgress ? "Updating…" : "Update"}
          </button>
        )}
        {connection && !isLive && !needsFolder && !syncInProgress && onCheckAgain && (
          <button
            className="button button-secondary"
            type="button"
            onClick={onCheckAgain}
            disabled={checkingChanges}
          >
            {checkingChanges ? "Checking…" : "Check"}
          </button>
        )}
      </div>
    </div>
  );
}
