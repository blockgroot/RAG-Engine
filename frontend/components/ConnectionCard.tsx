import { useEffect, useState } from "react";
import { api, ApiError, ConnectionRecord, JobRecord, SyncChanges } from "@/lib/api";
import { syncPagesDetail, syncPercent, syncPhaseHeadline } from "@/lib/syncProgress";
import { DriveFolderPicker } from "./DriveFolderPicker";
import { SlackChannelPicker } from "./SlackChannelPicker";
import { SlackMemberInvitePicker } from "./SlackMemberInvitePicker";
import { JobStatusBadge } from "./JobStatusBadge";
import { ProviderMark } from "./ProviderMark";

const PROVIDER_LABELS: Record<string, string> = {
  notion: "Notion",
  google: "Google Drive",
  github: "GitHub",
  slack: "Slack",
};

const SOURCE_NOUN: Record<string, string> = {
  notion: "Notion",
  google: "Drive",
  github: "GitHub",
  slack: "Slack",
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
  // hour12 forced explicitly — without it, toLocaleTimeString falls back to
  // the browser's locale default, which is 24-hour in plenty of locales
  // (e.g. en-GB) even when the rest of the product's copy assumes 12-hour.
  const time = d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
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


/** Provider-side screens where the admin adds/removes what Folio can see. */
function githubInstallSettingsUrl(installationId: string): string {
  return `https://github.com/settings/installations/${encodeURIComponent(installationId)}`;
}

function googleFolderUrl(folderId: string): string {
  return `https://drive.google.com/drive/folders/${encodeURIComponent(folderId)}`;
}

/** Notion has no single "pick pages" URL — sharing is per-page in the app. */
const NOTION_MANAGE_URL = "https://www.notion.so";

type Provider = "notion" | "google" | "github" | "slack";

function manageExternalHref(
  provider: Provider,
  connection: ConnectionRecord
): string | null {
  if (provider === "github") {
    const id = connection.source_config?.installation_id;
    return id ? githubInstallSettingsUrl(id) : null;
  }
  if (provider === "google") {
    const folderId = connection.source_config?.folder_id;
    return folderId
      ? googleFolderUrl(folderId)
      : "https://drive.google.com/drive/my-drive";
  }
  if (provider === "notion") {
    return NOTION_MANAGE_URL;
  }
  // Slack has no single "manage channels" URL -- membership/invites happen
  // per-channel inside Slack itself, same reason Notion has none either.
  return null;
}

function manageExternalLabel(provider: Provider): string {
  if (provider === "github") return "Manage on GitHub";
  if (provider === "google") return "Manage on Drive";
  return "Manage in Notion";
}

function manageExternalTitle(provider: Provider): string {
  if (provider === "github") {
    return "Open GitHub to add or remove repositories for this install";
  }
  if (provider === "google") {
    return "Open the linked Drive folder to add or remove files inside it";
  }
  return "Open Notion to share or unshare pages with this integration";
}

/**
 * Provider card: connected sources no longer show a blunt "Sync now" that
 * re-dumps everything. Instead we surface a change notice when remote pages
 * differ, and "Update policies" runs an incremental upsert.
 *
 * Google also needs a folder URL before any sync — Drive has no Notion-style
 * "whatever was shared with the integration" boundary. Once a folder is set,
 * "Change folder" reuses the same config PUT (org or workspace) so the admin
 * can repoint scope without reconnecting OAuth.
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
  onDisconnected,
  needsReauth = false,
  onNeedsReauth,
  workspaceId,
  onMembersInvited,
}: {
  provider: Provider;
  connection: ConnectionRecord | undefined;
  lastJob?: JobRecord;
  changes?: SyncChanges | null;
  checkingChanges?: boolean;
  onUpdate: (connectionId: string) => void;
  onCheckAgain?: () => void;
  onConfigSaved?: (connection: ConnectionRecord) => void;
  /** After Disconnect — parent drops the connection from local state. */
  onDisconnected?: (connectionId: string) => void;
  /** Parent sets this when Check/Update got oauth_reauth_required. */
  needsReauth?: boolean;
  onNeedsReauth?: (needed: boolean) => void;
  /** When set, this card manages a personal sub-workspace connection instead
   * of the org-wide one (Workspace-within-a-Workspace) -- connect/config
   * calls are routed to the workspace-scoped endpoints. */
  workspaceId?: string;
  /** After a Slack member-invite picker successfully adds someone. */
  onMembersInvited?: () => void;
}) {
  const available =
    provider === "notion" || provider === "google" || provider === "github" || provider === "slack";
  // GitHub is a "live" source: nothing is ever fetched, chunked, embedded, or
  // stored, so this card must hide every ingestion-shaped control (sync status,
  // change counts, Update/Check). Showing them would promise a sync that does
  // not exist -- and the API refuses those calls for GitHub anyway.
  const isLive = provider === "github";
  const syncInProgress = !isLive && lastJob != null && ACTIVE.has(lastJob.status);
  const needsUpdate = !isLive && Boolean(changes?.has_changes);
  const folderConfigured = Boolean(connection?.source_config?.folder_id);
  const needsFolder = provider === "google" && connection && !folderConfigured;
  const channelIds = connection?.source_config?.channel_ids ?? [];
  const channelsConfigured = channelIds.length > 0;
  const needsChannels = provider === "slack" && connection && !channelsConfigured;
  const [changingFolder, setChangingFolder] = useState(false);
  const [changingChannels, setChangingChannels] = useState(false);
  const [invitingMembers, setInvitingMembers] = useState(false);
  const [folderHint, setFolderHint] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);
  const [disconnectError, setDisconnectError] = useState<string | null>(null);

  // What the admin actually authorized on GitHub's install screen.
  const repoSelection = connection?.source_config?.repository_selection;
  const repos = connection?.source_config?.repos ?? [];
  const installationId = connection?.source_config?.installation_id;
  const manageHref = connection ? manageExternalHref(provider, connection) : null;
  const [refreshingScope, setRefreshingScope] = useState(false);
  const [scopeError, setScopeError] = useState<string | null>(null);
  /** Set after a successful GitHub "Refresh list" so we can show Up to date. */
  const [githubListFresh, setGithubListFresh] = useState(false);

  async function refreshScope() {
    if (!connection) return;
    setRefreshingScope(true);
    setScopeError(null);
    try {
      // Routed to the workspace endpoint when this card manages a workspace's
      // own connection, so a refresh can never re-read the org-wide scope into
      // a workspace row (or vice versa).
      const scope = workspaceId
        ? await api.refreshWorkspaceConnectionScope(workspaceId, connection.id)
        : await api.refreshConnectionScope(connection.id);
      onConfigSaved?.({
        ...connection,
        source_config: {
          ...connection.source_config,
          repository_selection: scope.repository_selection,
          repos: scope.repos,
        },
      });
      setGithubListFresh(true);
    } catch (err) {
      setGithubListFresh(false);
      noteReauth(err);
      setScopeError(err instanceof Error ? err.message : "Could not refresh repositories.");
    } finally {
      setRefreshingScope(false);
    }
  }

  const [configError, setConfigError] = useState<string | null>(null);

  // Drop the post-change hint once an Update is running — the job badge takes over.
  useEffect(() => {
    if (syncInProgress) setFolderHint(null);
  }, [syncInProgress]);

  // Docs: after Check with no remote changes — same "Up to date" chip as a
  // successful sync job. Prefer not to claim up-to-date while an update is due.
  const docsCheckedFresh =
    !isLive &&
    Boolean(connection) &&
    !needsFolder &&
    !needsChannels &&
    !syncInProgress &&
    !checkingChanges &&
    !needsUpdate &&
    changes != null &&
    !changes.has_changes;

  // While Check is in flight, hide a stale "Up to date" so the Checking… line is clear.
  const showDocsJobBadge =
    !isLive && Boolean(lastJob) && !needsUpdate && !checkingChanges;
  const showGithubUpToDate =
    isLive && Boolean(connection) && githubListFresh && !refreshingScope && !scopeError;

  async function handleDisconnect() {
    if (!connection || disconnecting) return;
    const label = PROVIDER_LABELS[provider];
    const ok = window.confirm(
      isLive
        ? `Disconnect ${label}? Live answers for this scope will stop until you connect again.`
        : `Disconnect ${label}? Indexed documents for this source will be deleted and answers will stop using them.`
    );
    if (!ok) return;
    setDisconnecting(true);
    setDisconnectError(null);
    try {
      if (workspaceId) {
        await api.disconnectWorkspaceConnection(workspaceId, connection.id);
      } else {
        await api.disconnectConnection(connection.id);
      }
      onDisconnected?.(connection.id);
    } catch (err) {
      setDisconnectError(err instanceof Error ? err.message : "Could not disconnect.");
    } finally {
      setDisconnecting(false);
    }
  }

  function noteReauth(err: unknown) {
    if (err instanceof ApiError && err.code === "oauth_reauth_required") {
      onNeedsReauth?.(true);
      return true;
    }
    return false;
  }

  return (
    <div className={`card source-studio-card source-studio-card--${provider}${connection ? " is-linked" : ""}`}>
      <div className="source-studio-top">
        <div className="source-studio-title">
          <ProviderMark provider={provider} />
          <div>
            <h3>{PROVIDER_LABELS[provider]}</h3>
            <p className="source-studio-kind">
              {provider === "github"
                ? workspaceId
                  ? "This space only — pick a different GitHub account than Company Sources"
                  : "Company-wide Code tab — usually a GitHub Organization"
                : isLive
                  ? "Live answers"
                  : "Synced documents"}
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
          {provider === "slack" && channelsConfigured
            ? ` · ${channelIds.length} channel${channelIds.length === 1 ? "" : "s"}`
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
                : "No repos linked yet — manage access on GitHub, then refresh the list."}
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
            To add or remove repos, manage access on GitHub, then Refresh list here.
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
              setFolderHint(null);
              onConfigSaved?.({ ...connection, source_config: config });
            }}
            onError={(message) => setConfigError(message || null)}
          />
          {configError && <div className="banner banner-warn">{configError}</div>}
        </div>
      )}

      {provider === "google" && connection && folderConfigured && changingFolder && (
        <div className="stack" style={{ marginTop: "0.9rem" }}>
          <DriveFolderPicker
            connectionId={connection.id}
            workspaceId={workspaceId}
            inputId={`folder-change-${provider}`}
            mode="change"
            currentFolderId={connection.source_config?.folder_id}
            currentFolderName={connection.source_config?.folder_name}
            onSaved={(config, meta) => {
              setConfigError(null);
              setChangingFolder(false);
              onNeedsReauth?.(false);
              const purged = meta?.documents_purged ?? 0;
              setFolderHint(
                meta?.folder_changed
                  ? purged > 0
                    ? `Folder updated · ${purged} old page${purged === 1 ? "" : "s"} removed. Run Update to index the new folder.`
                    : "Folder updated. Run Update to index the new folder."
                  : "Folder saved."
              );
              onConfigSaved?.({ ...connection, source_config: config });
            }}
            onError={(message) => setConfigError(message || null)}
            onCancel={() => {
              setChangingFolder(false);
              setConfigError(null);
            }}
          />
          {configError && <div className="banner banner-warn">{configError}</div>}
        </div>
      )}

      {needsChannels && connection && (
        <div className="stack" style={{ marginTop: "0.9rem" }}>
          <SlackChannelPicker
            connectionId={connection.id}
            workspaceId={workspaceId}
            onSaved={(config) => {
              setConfigError(null);
              setFolderHint(null);
              onConfigSaved?.({ ...connection, source_config: config });
            }}
            onError={(message) => setConfigError(message || null)}
          />
          {configError && <div className="banner banner-warn">{configError}</div>}
        </div>
      )}

      {provider === "slack" && connection && channelsConfigured && changingChannels && (
        <div className="stack" style={{ marginTop: "0.9rem" }}>
          <SlackChannelPicker
            connectionId={connection.id}
            workspaceId={workspaceId}
            currentChannelIds={channelIds}
            onSaved={(config, meta) => {
              setConfigError(null);
              setChangingChannels(false);
              onNeedsReauth?.(false);
              const purged = meta?.documents_purged ?? 0;
              setFolderHint(
                meta?.channels_changed
                  ? purged > 0
                    ? `Channels updated · ${purged} old thread${purged === 1 ? "" : "s"} removed. Run Update to index the new selection.`
                    : "Channels updated. Run Update to index the new selection."
                  : "Channels saved."
              );
              onConfigSaved?.({ ...connection, source_config: config });
            }}
            onError={(message) => setConfigError(message || null)}
            onCancel={() => {
              setChangingChannels(false);
              setConfigError(null);
            }}
          />
          {configError && <div className="banner banner-warn">{configError}</div>}
        </div>
      )}

      {provider === "slack" &&
        workspaceId &&
        connection &&
        channelsConfigured &&
        !changingChannels &&
        invitingMembers && (
          <div className="stack" style={{ marginTop: "0.9rem" }}>
            <SlackMemberInvitePicker
              workspaceId={workspaceId}
              connectionId={connection.id}
              channelIds={channelIds}
              channelNames={connection.source_config?.channel_names}
              onInvited={onMembersInvited}
            />
          </div>
        )}

      {folderHint && !changingFolder && !changingChannels && (
        <p className="muted" style={{ marginTop: "0.65rem" }}>
          {folderHint}
        </p>
      )}

      {showDocsJobBadge && lastJob && (
        <div className="stack" style={{ marginTop: "0.55rem", gap: "0.35rem" }}>
          <p
            className="muted"
            style={{ margin: 0, display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}
          >
            <JobStatusBadge status={lastJob.status} />
            {ACTIVE.has(lastJob.status)
              ? syncPhaseHeadline(lastJob)
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
          {ACTIVE.has(lastJob.status) && (
            <>
              <p className="muted" style={{ margin: 0, fontSize: "0.85rem" }}>
                {syncPagesDetail(lastJob)}
              </p>
              {syncPercent(lastJob) != null && (
                <div
                  className="sync-progress"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={syncPercent(lastJob) ?? 0}
                >
                  <div
                    className="sync-progress-bar"
                    style={{ width: `${syncPercent(lastJob)}%` }}
                  />
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Check found nothing new, and there is no job badge yet (e.g. Drive). */}
      {docsCheckedFresh && !showDocsJobBadge && (
        <p
          className="muted"
          style={{ marginTop: "0.55rem", display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}
        >
          <span className="badge badge-verified">Up to date</span>
          <span>No changes</span>
        </p>
      )}

      {showGithubUpToDate && (
        <p
          className="muted"
          style={{ marginTop: "0.55rem", display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }}
        >
          <span className="badge badge-verified">Up to date</span>
          <span>Repo list refreshed</span>
        </p>
      )}

      {connection && !needsFolder && !needsChannels && needsUpdate && !syncInProgress && (
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

      {connection && checkingChanges && !isLive && !needsFolder && !needsChannels && (
        <p className="muted" style={{ marginTop: "0.65rem" }}>
          Checking…
        </p>
      )}

      {needsReauth && connection && (
        <div className="banner banner-warn" style={{ marginTop: "0.75rem" }} role="alert">
          Access expired — reconnect {PROVIDER_LABELS[provider]} to continue.
          {connection.reauth_reason ? (
            <span className="muted" style={{ display: "block", marginTop: "0.35rem" }}>
              {connection.reauth_reason}
            </span>
          ) : null}
        </div>
      )}
      {disconnectError && (
        <div className="banner banner-warn" style={{ marginTop: "0.75rem" }} role="alert">
          {disconnectError}
        </div>
      )}

      <div className="source-card-actions">
        {available && !connection && (
          <a
            className="button"
            href={workspaceId ? api.connectWorkspaceUrl(workspaceId, provider) : api.connectUrl(provider)}
          >
            {provider === "github"
              ? workspaceId
                ? "Connect personal GitHub"
                : "Connect company GitHub"
              : `Connect ${PROVIDER_LABELS[provider]}`}
          </a>
        )}
        {connection && manageHref && (
          <a
            className="button button-secondary"
            href={manageHref}
            target="_blank"
            rel="noreferrer"
            title={manageExternalTitle(provider)}
          >
            {manageExternalLabel(provider)}
          </a>
        )}
        {provider === "google" &&
          connection &&
          folderConfigured &&
          !changingFolder &&
          !syncInProgress && (
            <button
              className="button button-secondary"
              type="button"
              onClick={() => {
                setChangingFolder(true);
                setFolderHint(null);
                setConfigError(null);
              }}
              title="Point this connection at a different Drive folder"
            >
              Change folder
            </button>
          )}
        {provider === "slack" &&
          connection &&
          channelsConfigured &&
          !changingChannels &&
          !syncInProgress && (
            <button
              className="button button-secondary"
              type="button"
              onClick={() => {
                setChangingChannels(true);
                setFolderHint(null);
                setConfigError(null);
              }}
              title="Add or remove connected Slack channels"
            >
              Change channels
            </button>
          )}
        {provider === "slack" &&
          workspaceId &&
          connection &&
          channelsConfigured &&
          !changingChannels &&
          !syncInProgress && (
            <button
              className="button button-secondary"
              type="button"
              onClick={() => {
                setInvitingMembers((v) => !v);
                setConfigError(null);
              }}
              title="Invite members of your connected Slack channel(s) to this space"
            >
              {invitingMembers ? "Hide invite" : "Invite members"}
            </button>
          )}
        {connection && isLive && (
          <button
            className="button button-secondary"
            type="button"
            onClick={refreshScope}
            disabled={refreshingScope}
            title="Pull the latest repo list after you change access on GitHub"
          >
            {refreshingScope ? "Refreshing…" : "Refresh list"}
          </button>
        )}
        {connection && !isLive && !needsFolder && !needsChannels && needsUpdate && (
          <button
            className="button"
            type="button"
            onClick={() => onUpdate(connection.id)}
            disabled={syncInProgress}
          >
            {syncInProgress ? "Updating…" : "Update"}
          </button>
        )}
        {connection && !isLive && !needsFolder && !needsChannels && !syncInProgress && onCheckAgain && (
          <button
            className="button button-secondary"
            type="button"
            onClick={onCheckAgain}
            disabled={checkingChanges}
          >
            {checkingChanges ? "Checking…" : "Check"}
          </button>
        )}
        {connection && needsReauth && (
          <a
            className="button"
            href={workspaceId ? api.connectWorkspaceUrl(workspaceId, provider) : api.connectUrl(provider)}
          >
            Reconnect {PROVIDER_LABELS[provider]}
          </a>
        )}
        {connection && !syncInProgress && !disconnecting && (
          <button
            className="button button-secondary"
            type="button"
            onClick={handleDisconnect}
            disabled={disconnecting}
            title="Remove this connection and its indexed documents"
          >
            {disconnecting ? "Disconnecting…" : "Disconnect"}
          </button>
        )}
      </div>
    </div>
  );
}
