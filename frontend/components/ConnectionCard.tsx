import { useEffect, useState } from "react";
import { api, ConnectionRecord, DriveFolder, JobRecord, SyncChanges } from "@/lib/api";
import { JobStatusBadge } from "./JobStatusBadge";

const FOLDER_SEARCH_DEBOUNCE_MS = 300;

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

  const [folderUrl, setFolderUrl] = useState("");
  const [savingConfig, setSavingConfig] = useState(false);
  const [configError, setConfigError] = useState<string | null>(null);

  // Drive folder-picker dropdown: search-as-you-type against folders the
  // connected account can see, so connecting a folder no longer requires
  // copy-pasting its URL out of Drive (a plain URL/id paste still works as a
  // fallback via "Save folder" below).
  const [folderResults, setFolderResults] = useState<DriveFolder[]>([]);
  const [searchingFolders, setSearchingFolders] = useState(false);
  const [folderSearchError, setFolderSearchError] = useState<string | null>(null);
  const [dropdownOpen, setDropdownOpen] = useState(false);

  useEffect(() => {
    if (!needsFolder || !dropdownOpen || !connection) return;
    let cancelled = false;
    setSearchingFolders(true);
    const timer = setTimeout(async () => {
      try {
        const { folders } = workspaceId
          ? await api.searchWorkspaceConnectionDriveFolders(workspaceId, connection.id, folderUrl.trim())
          : await api.searchConnectionDriveFolders(connection.id, folderUrl.trim());
        if (cancelled) return;
        setFolderResults(folders);
        setFolderSearchError(null);
      } catch (err) {
        if (cancelled) return;
        setFolderResults([]);
        setFolderSearchError(err instanceof Error ? err.message : "Could not search Drive folders.");
      } finally {
        if (!cancelled) setSearchingFolders(false);
      }
    }, FOLDER_SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folderUrl, dropdownOpen, needsFolder, connection?.id, workspaceId]);

  async function saveFolder(value: string) {
    if (!connection || !value) return;
    setSavingConfig(true);
    setConfigError(null);
    try {
      const result = workspaceId
        ? await api.setWorkspaceConnectionConfig(workspaceId, connection.id, value)
        : await api.setConnectionConfig(connection.id, value);
      setFolderUrl("");
      setFolderResults([]);
      setDropdownOpen(false);
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

  function handleSaveFolder(e: React.FormEvent) {
    e.preventDefault();
    saveFolder(folderUrl.trim());
  }

  function handleSelectFolder(folder: DriveFolder) {
    saveFolder(folder.id);
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
        <div className="stack" style={{ marginTop: "0.9rem", gap: "0.55rem" }}>
          <p className="muted" style={{ margin: 0 }}>
            {repoSelection === "all"
              ? "All repositories in this organization are available."
              : repos.length > 0
                ? `${repos.length} repositor${repos.length === 1 ? "y" : "ies"} available.`
                : "No repositories are available yet — refresh, or add them to the installation on GitHub."}
          </p>
          {repoSelection !== "all" && repos.length > 0 && (
            <p className="muted" style={{ margin: 0, fontSize: "0.85rem" }}>
              {repos
                .slice(0, 5)
                .map((r) => r.full_name)
                .join(", ")}
              {repos.length > 5 ? ` +${repos.length - 5} more` : ""}
            </p>
          )}
          <p className="muted" style={{ margin: 0, fontSize: "0.85rem" }}>
            Answers are read live from GitHub when a question is asked, so
            nothing is stored or needs syncing. Which repositories are included
            is chosen on GitHub.
          </p>
          {scopeError && <div className="banner banner-warn">{scopeError}</div>}
        </div>
      )}

      {needsFolder && (
        <form onSubmit={handleSaveFolder} className="stack" style={{ marginTop: "0.9rem" }}>
          <p className="muted" style={{ margin: 0 }}>
            Choose a Drive folder to use.
          </p>
          <div className="field" style={{ position: "relative" }}>
            <label htmlFor={`folder-${provider}`}>Folder</label>
            <input
              id={`folder-${provider}`}
              className="input"
              type="text"
              required
              autoComplete="off"
              placeholder="Search folders or paste a link…"
              value={folderUrl}
              onChange={(e) => setFolderUrl(e.target.value)}
              onFocus={() => setDropdownOpen(true)}
              onBlur={() => setTimeout(() => setDropdownOpen(false), 150)}
            />
            {dropdownOpen && (searchingFolders || folderResults.length > 0 || folderSearchError) && (
              <div className="folder-dropdown" role="listbox">
                {searchingFolders && <div className="folder-dropdown-status">Searching…</div>}
                {!searchingFolders && folderSearchError && (
                  <div className="folder-dropdown-status">{folderSearchError}</div>
                )}
                {!searchingFolders && !folderSearchError && folderResults.length === 0 && (
                  <div className="folder-dropdown-status">
                    No matches — paste a folder link instead.
                  </div>
                )}
                {!searchingFolders &&
                  folderResults.map((folder) => (
                    <button
                      key={folder.id}
                      type="button"
                      className="folder-dropdown-item"
                      // Keep the input focused so onBlur doesn't close the
                      // dropdown before this click registers.
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => handleSelectFolder(folder)}
                    >
                      {folder.name}
                    </button>
                  ))}
              </div>
            )}
          </div>
          {configError && <div className="banner banner-warn">{configError}</div>}
          <button className="button" type="submit" disabled={savingConfig || !folderUrl.trim()}>
            {savingConfig ? "Saving…" : "Save folder"}
          </button>
        </form>
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

      <div style={{ marginTop: "1rem", display: "flex", gap: "0.6rem", flexWrap: "wrap" }}>
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
            className="button-secondary"
            type="button"
            onClick={refreshScope}
            disabled={refreshingScope}
            title="Re-read which repositories this installation can see"
          >
            {refreshingScope ? "Refreshing…" : "Refresh repositories"}
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
            className="button-secondary"
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
