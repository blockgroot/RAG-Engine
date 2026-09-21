import { useEffect, useState } from "react";
import {
  api,
  ApiError,
  ConnectionRecord,
  JobRecord,
  SharingWarning,
  SyncChanges,
} from "@/lib/api";
import { syncPagesDetail, syncPercent, syncPhaseHeadline } from "@/lib/syncProgress";
import { formatReauthReason } from "@/lib/reauthReason";
import { DriveFolderPicker } from "./DriveFolderPicker";
import { SlackChannelPicker } from "./SlackChannelPicker";
import { SlackMemberInvitePicker } from "./SlackMemberInvitePicker";
import { JobStatusBadge } from "./JobStatusBadge";
import { BrandGlyph, type BrandName } from "./BrandGlyph";

const PROVIDER_LABELS: Record<string, string> = {
  notion: "Notion",
  google: "Google Drive",
  github: "GitHub",
  slack: "Slack",
  linear: "Linear",
};

const BRAND_GLYPH: Record<string, BrandName> = {
  notion: "notion",
  google: "drive",
  github: "github",
  slack: "slack",
  linear: "linear",
};

const SOURCE_NOUN: Record<string, string> = {
  notion: "Notion",
  google: "Drive",
  github: "GitHub",
  slack: "Slack",
  linear: "Linear",
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


function githubInstallSettingsUrl(installationId: string): string {
  return `https://github.com/settings/installations/${encodeURIComponent(installationId)}`;
}

function googleFolderUrl(folderId: string): string {
  return `https://drive.google.com/drive/folders/${encodeURIComponent(folderId)}`;
}

const NOTION_MANAGE_URL = "https://www.notion.so";

const LINEAR_MANAGE_URL = "https://linear.app/settings/integrations";

type Provider = "notion" | "google" | "github" | "slack" | "linear";

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
  if (provider === "linear") {
    return LINEAR_MANAGE_URL;
  }
  return null;
}

function manageExternalLabel(): string {
  return "Manage";
}

function manageExternalTitle(provider: Provider): string {
  if (provider === "github") {
    return "Open GitHub to add or remove repositories for this install";
  }
  if (provider === "google") {
    return "Open the linked Drive folder to add or remove files inside it";
  }
  if (provider === "linear") {
    return "Open Linear to review or revoke this integration's access";
  }
  return "Open Notion to share or unshare pages with this integration";
}

/** "last checked 12 minutes ago" — the freshness signal that replaced Check.
 *
 * Automatic sync (``app/jobs/autosync.py``) stamps ``last_sync_at`` on every
 * ATTEMPT, so this answers "is the background sync alive?" and nothing more:
 * a stamp is not a promise that anything was ingested. That is exactly the
 * question the removed Check button used to answer by hand.
 */
function checkedAgo(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (!Number.isFinite(minutes) || minutes < 0) return "just now";
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"} ago`;
}

export function ConnectionCard({
  provider,
  connection,
  lastJob,
  changes,
  checkingChanges,
  onUpdate,
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
  onConfigSaved?: (connection: ConnectionRecord) => void;
  onDisconnected?: (connectionId: string) => void;
  needsReauth?: boolean;
  onNeedsReauth?: (needed: boolean) => void;
  workspaceId?: string;
  onMembersInvited?: () => void;
}) {
  const available =
    provider === "notion" ||
    provider === "google" ||
    provider === "github" ||
    provider === "slack" ||
    provider === "linear";
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
  // Files in the saved folder Handbook cannot index because Google will not
  // say who they are shared with. Held on the CARD rather than in the picker
  // because the picker closes on save and this has to outlive it.
  const [sharing, setSharing] = useState<SharingWarning | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);
  const [disconnectError, setDisconnectError] = useState<string | null>(null);

  const repoSelection = connection?.source_config?.repository_selection;
  const repos = connection?.source_config?.repos ?? [];
  const installationId = connection?.source_config?.installation_id;
  const manageHref = connection ? manageExternalHref(provider, connection) : null;
  const [refreshingScope, setRefreshingScope] = useState(false);
  const [scopeError, setScopeError] = useState<string | null>(null);
  const [githubListFresh, setGithubListFresh] = useState(false);

  async function refreshScope() {
    if (!connection) return;
    setRefreshingScope(true);
    setScopeError(null);
    try {
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

  useEffect(() => {
    if (syncInProgress) setFolderHint(null);
  }, [syncInProgress]);

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
    <div className={`card source-row source-row--${provider}${connection ? " is-linked" : ""}`}>
      <div className="source-row-head">
        <div className="source-row-main">
          <span className="source-row-mark">
            <BrandGlyph name={BRAND_GLYPH[provider]} size={28} />
          </span>
          <div className="source-row-copy">
            <h3>{PROVIDER_LABELS[provider]}</h3>
            <p className="source-row-kind">
              {provider === "github"
                ? workspaceId
                  ? "This space only"
                  : "Company-wide Code tab"
                : isLive
                  ? "Live answers"
                  : "Synced documents"}
              {connection && (
                <>
                  {" · "}
                  {connection.external_workspace_name || "Connected"}
                  {provider === "google" && folderConfigured
                    ? ` · ${connection.source_config?.folder_name || "Drive folder"}`
                    : ""}
                  {provider === "slack" && channelsConfigured
                    ? ` · ${channelIds.length} channel${channelIds.length === 1 ? "" : "s"}`
                    : ""}
                </>
              )}
            </p>
          </div>
        </div>

        {connection ? (
          <span className="badge badge-verified">Linked</span>
        ) : (
          <span className="badge">{available ? "Not linked yet" : "Coming soon"}</span>
        )}

        <div className="source-row-actions">
          {available && !connection && (
            <a
              className="button"
              href={workspaceId ? api.connectWorkspaceUrl(workspaceId, provider) : api.connectUrl(provider)}
            >
              {provider === "github"
                ? workspaceId
                  ? "Connect personal account"
                  : "Connect company account"
                : "Connect"}
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
              {manageExternalLabel()}
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
          {connection && needsReauth && (
            <a
              className="button"
              href={workspaceId ? api.connectWorkspaceUrl(workspaceId, provider) : api.connectUrl(provider)}
            >
              Reconnect
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
            onSaved={(config, meta) => {
              setConfigError(null);
              setFolderHint(null);
              setSharing(meta?.sharing ?? null);
              onConfigSaved?.({ ...connection, source_config: config });
            }}
            onError={(message) => setConfigError(message || null)}
          />
          {configError && <div className="banner banner-warn">{configError}</div>}
        </div>
      )}


      {/* A freshly connected Slack has no channels yet, and every OTHER Slack
          picker branch below is gated on `channelsConfigured` -- so without
          this one there is nothing to click and the card reads as "Linked"
          while indexing nothing. Drive has always had the equivalent
          (`needsFolder` above); Slack was simply never given it. */}
      {needsChannels && connection && (
        <div className="stack" style={{ marginTop: "0.9rem" }}>
          <p className="muted" style={{ margin: 0 }}>
            Pick the channels Handbook may read. Nothing is indexed until you choose.
          </p>
          <SlackChannelPicker
            connectionId={connection.id}
            workspaceId={workspaceId}
            currentChannelIds={channelIds}
            onSaved={(config) => {
              setConfigError(null);
              setFolderHint("Channels saved. Indexing has been queued.");
              onNeedsReauth?.(false);
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

      {showDocsJobBadge && !needsReauth && lastJob && (
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

      {connection && !needsReauth && !needsFolder && !needsChannels && !syncInProgress && !needsUpdate && (
        <p className="muted" style={{ marginTop: "0.55rem" }}>
          {connection.last_sync_at
            ? `Syncs automatically · last checked ${checkedAgo(connection.last_sync_at)}`
            : "Syncs automatically · first check due within the hour"}
        </p>
      )}

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
        <div className="connection-reauth-box" role="alert">
          <span className="connection-reauth-ico" aria-hidden>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path
                d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <div className="connection-reauth-body">
            <span className="connection-reauth-title">Access expired</span>
            <p className="connection-reauth-desc">
              {formatReauthReason(provider, connection.reauth_reason)}
            </p>
          </div>
        </div>
      )}
      {/* Shown the moment the folder is saved, to the person who saved it --
          an admin for the company, a space's OWNER for their own space. A
          folder whose sharing Handbook cannot read is that person's problem to
          fix, and this is the one moment they are looking at it. Same shape as
          the expired-access box because it is the same kind of thing: a
          working connection with something in it that needs a human. */}
      {/* The same box, from the last SYNC rather than the folder picker.
          Sharing can be tightened long after someone chose the folder, and the
          preflight structurally cannot see that happen — so the count the sync
          recorded is what covers the drift. Shown only when the picker has not
          just said the same thing, so one problem is stated once. */}
      {!sharing &&
        lastJob?.status === "succeeded" &&
        (lastJob.permission_unreadable_documents ?? 0) > 0 && (
          <div className="connection-reauth-box" role="alert">
            <span className="connection-reauth-ico" aria-hidden>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                <path
                  d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            <div className="connection-reauth-body">
              <span className="connection-reauth-title">
                {lastJob.permission_unreadable_documents}{" "}
                {lastJob.permission_unreadable_documents === 1 ? "file" : "files"} couldn’t
                be added
              </span>
              <p className="connection-reauth-desc">
                The last sync couldn’t see who they’re shared with, so Handbook left them
                out rather than risk showing them to the wrong people. Make the connected
                account an owner or editor of the folder, then run Update.
              </p>
            </div>
          </div>
        )}

      {sharing && (
        <div className="connection-reauth-box" role="alert">
          <span className="connection-reauth-ico" aria-hidden>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path
                d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </span>
          <div className="connection-reauth-body">
            <span className="connection-reauth-title">{sharing.title}</span>
            <p className="connection-reauth-desc">{sharing.detail}</p>
            {sharing.files.length > 0 && (
              // Named on purpose, unlike the notification bell: whoever is
              // choosing this folder can already open it in Drive, so listing
              // what is in it discloses nothing -- and "which ones?" is the
              // immediate next question.
              <p className="connection-reauth-desc">
                {sharing.files.join(", ")}
                {sharing.count > sharing.files.length
                  ? ` and ${sharing.count - sharing.files.length} more`
                  : ""}
              </p>
            )}
            <p className="connection-reauth-desc">{sharing.fix}</p>
          </div>
        </div>
      )}
      {disconnectError && (
        <div className="banner banner-warn" style={{ marginTop: "0.75rem" }} role="alert">
          {disconnectError}
        </div>
      )}
    </div>
  );
}
